"""DB-authoritative, bounded policy publication; publication is NOT broker proof.

A single API-process worker owns publication. SQLite and historical application
naive timestamps mean UTC (the same convention as device authentication).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from sqlalchemy import or_, select

from app.db.models import Certificate, DeviceCredential, Repeater
from app.services.mqtt_broker_policy import (
    MAX_ACTIVE_DEVICES,
    MAX_REVOKED_CERTIFICATES,
    build_acl,
    build_crl,
)
from app.services.mqtt_identity import canonical_device_id

logger = logging.getLogger(__name__)
BROKER_GID = 1883


def db_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("invalid policy timestamp")
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def serial_hex(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{1,40}", value):
        raise ValueError("invalid policy serial")
    number = int(value, 16)
    if number <= 0 or number.bit_length() > 159:
        raise ValueError("invalid policy serial")
    return format(number, "x")


@dataclass(frozen=True)
class PolicySnapshot:
    active_device_ids: tuple[str, ...]
    revoked: tuple[tuple[str, datetime], ...]


def database_policy(db, now: datetime) -> PolicySnapshot:
    now = db_utc(now)
    # Query only bounded public policy fields, never token hashes or PEMs.
    rows = db.execute(
        select(
            Repeater.id,
            Repeater.cert_serial,
            DeviceCredential.cert_serial,
            Certificate.serial,
            Certificate.cn,
            Certificate.issued_at,
            Certificate.expires_at,
        )
        .join(DeviceCredential, DeviceCredential.repeater_id == Repeater.id)
        .outerjoin(
            Certificate,
            (Certificate.repeater_id == Repeater.id)
            & (Certificate.serial == DeviceCredential.cert_serial),
        )
        .where(
            Repeater.status.in_(("adopted", "connected", "offline")),
            DeviceCredential.revoked_at.is_(None),
            Certificate.revoked_at.is_(None),
        )
        .limit(MAX_ACTIVE_DEVICES + 1)
    ).all()
    if len(rows) > MAX_ACTIVE_DEVICES:
        raise ValueError("too many policy devices")
    active = []
    for device_id, parent, credential, serial, cn, issued, expires in rows:
        canonical_device_id(device_id)
        values = [serial_hex(credential)]
        if serial is not None:
            values.append(serial_hex(serial))
        if parent is not None:
            values.append(serial_hex(parent))
        if (
            serial is not None
            and parent is not None
            and len(set(values)) == 1
            and cn == "device:" + device_id
            and db_utc(issued) <= now < db_utc(expires)
        ):
            active.append(device_id)
    # Credential revocation invalidates ALL its certificates, including old
    # sessions after rotation. Serial-only revocation leaves newer leaves usable.
    rows = db.execute(
        select(Certificate.serial, Certificate.revoked_at, DeviceCredential.revoked_at)
        .outerjoin(DeviceCredential, DeviceCredential.repeater_id == Certificate.repeater_id)
        .where(or_(Certificate.revoked_at.is_not(None), DeviceCredential.revoked_at.is_not(None)))
        .limit(MAX_REVOKED_CERTIFICATES + 1)
    ).all()
    if len(rows) > MAX_REVOKED_CERTIFICATES:
        raise ValueError("too many policy revocations")
    revoked = []
    for serial, cert_time, credential_time in rows:
        times = [db_utc(t) for t in (cert_time, credential_time) if t is not None]
        if any(t > now for t in times):
            raise ValueError("future policy revocation")
        when = min(times)
        revoked.append((serial_hex(serial), when.replace(microsecond=0)))
    # Validate duplicates/canonical UUIDs with the existing pure builder.
    build_acl(active)
    if len({s for s, _ in revoked}) != len(revoked):
        raise ValueError("duplicate policy revocation")
    return PolicySnapshot(tuple(sorted(active)), tuple(sorted(revoked)))


def broker_permissions(path: Path, mode: int) -> None:
    # Fixed GID, no caller-supplied ownership API. Nonroot callers must already
    # own the file and belong to this group; any failure is propagated.
    os.chown(path, -1, BROKER_GID)
    os.chmod(path, mode)
    stat = path.stat()
    if stat.st_gid != BROKER_GID or stat.st_mode & 0o777 != mode:
        raise PermissionError("broker asset permissions not established")


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def stage_file(path: Path, data: bytes, mode: int = 0o640) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        broker_permissions(path, mode)
        os.fsync(stream.fileno())


class BrokerPolicyPublisher:
    """One atomic current symlink switches the staged ACL/CRL pair.

    DB snapshot, files and broker activation are NOT one transaction. Before
    replace errors preserve last good. After replace/fsync errors are uncertain
    and must be retried from actual files, never reported as broker confirmation.
    """

    def __init__(self, pki):
        self.pki = pki
        self.root = pki.broker_directory

    def publish(self, snapshot: PolicySnapshot, now: datetime) -> bool:
        now = db_utc(now)
        acl = build_acl(snapshot.active_device_ids).encode("ascii")
        ca, key = self.pki.broker_signing_material()
        current = self.root / "current"
        crl = None
        try:
            old = x509.load_pem_x509_crl((current / "crl.pem").read_bytes())
            entries = tuple(
                sorted((format(r.serial_number, "x"), r.revocation_date_utc) for r in old)
            )
            if (
                old.issuer == ca.subject
                and old.is_signature_valid(ca.public_key())
                and old.last_update_utc <= now
                and old.next_update_utc > now + timedelta(hours=1)
                and entries == snapshot.revoked
            ):
                crl = old
        except FileNotFoundError:
            pass
        # Invalid existing policy is an observable failure, not a permissive fallback.
        if crl is None:
            crl = build_crl(ca, key, snapshot.revoked, now)
        crl_bytes = crl.public_bytes(serialization.Encoding.PEM)
        if (
            current.exists()
            and (current / "acl").read_bytes() == acl
            and (current / "crl.pem").read_bytes() == crl_bytes
        ):
            # Also resolves a previous post-replace fsync uncertainty without
            # changing CRL bytes or restarting the broker.
            sync_directory(current.resolve())
            sync_directory(self.root)
            return False
        self.root.mkdir(parents=True, exist_ok=True)
        broker_permissions(self.root, 0o750)
        generation = Path(tempfile.mkdtemp(prefix="policy-", dir=self.root))
        link = self.root / ("." + generation.name)
        switched = False
        previous = current.resolve() if current.exists() else None
        try:
            broker_permissions(generation, 0o750)
            stage_file(generation / "acl", acl)
            stage_file(generation / "crl.pem", crl_bytes)
            sync_directory(generation)
            os.symlink(generation.name, link)
            os.replace(link, current)
            switched = True
            sync_directory(self.root)
        finally:
            link.unlink(missing_ok=True)
            if not switched:
                shutil.rmtree(generation)
        # Bound disk usage; retain one previous generation for diagnosis.
        for old in self.root.glob("policy-*"):
            if old != generation and old != previous and old.is_dir() and not old.is_symlink():
                shutil.rmtree(old)
        return True


class BrokerPolicyService:
    def __init__(self, settings, session_factory, pki):
        self.settings = settings
        self.session_factory = session_factory
        self.publisher = BrokerPolicyPublisher(pki)
        self.task = None
        self.last_error = None
        self.last_publication_at = None
        self._stop = asyncio.Event()

    def refresh(self) -> None:
        try:
            now = datetime.now(UTC)
            with self.session_factory() as db:
                # Snapshot is bounded; no claim of cross-query serializability.
                snapshot = database_policy(db, now)
            self.publisher.publish(snapshot, now)
            self.last_publication_at = now
            self.last_error = None
        except Exception as exc:
            # Never log exception text/tracebacks: DB exceptions can contain
            # connection strings, SQL parameters and credentials.
            self.last_error = type(exc).__name__
            logger.error(
                "Broker policy publication failed (%s); activation unconfirmed", self.last_error
            )

    async def start(self) -> None:
        if not self.settings.broker_policy_enabled or self.task is not None:
            return
        await asyncio.to_thread(self.refresh)
        self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(
                    self._stop.wait(), self.settings.broker_policy_interval_seconds
                )
            except TimeoutError:
                await asyncio.to_thread(self.refresh)

    async def stop(self) -> None:
        self._stop.set()
        if self.task is not None:
            await self.task
            self.task = None
