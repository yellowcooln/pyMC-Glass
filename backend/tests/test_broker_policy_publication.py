import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.config import Settings
from app.db.base import Base
from app.db.models import Certificate, DeviceCredential, Repeater
from app.services import broker_policy_publication as publication
from app.services.broker_policy_publication import database_policy
from app.services.pki import PkiService
from cryptography import x509
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

A = "00000000-0000-4000-8000-000000000001"
NOW = datetime(2026, 4, 15, tzinfo=UTC)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Repeater(
                id=A, node_name="not-authority", pubkey="a", status="adopted", cert_serial="ff"
            )
        )
        session.add(
            DeviceCredential(
                repeater_id=A, token_hash="a", csr_public_key_sha256="b", cert_serial="ff"
            )
        )
        session.add(
            Certificate(
                repeater_id=A,
                serial="ff",
                cn="device:" + A,
                issued_at=NOW - timedelta(days=1),
                expires_at=NOW + timedelta(days=1),
            )
        )
        session.commit()
        yield session


def test_eligible_and_naive_sqlite_utc(db):
    assert database_policy(db, NOW).active_device_ids == (A,)


@pytest.mark.parametrize("status", ["pending", "revoked", "rejected"])
def test_ineligible_status(db, status):
    db.get(Repeater, A).status = status
    db.commit()
    assert database_policy(db, NOW).active_device_ids == ()


@pytest.mark.parametrize("target", ["credential", "certificate"])
def test_revocation_removes_acl_and_adds_serial(db, target):
    obj = db.get(DeviceCredential, A) if target == "credential" else db.query(Certificate).one()
    obj.revoked_at = NOW
    db.commit()
    policy = database_policy(db, NOW)
    assert policy.active_device_ids == ()
    assert policy.revoked == (("ff", NOW),)


def test_current_serial_parent_cn_expiry_agreement(db):
    cert = db.query(Certificate).one()
    for field, bad in [
        ("cn", "repeater:not-authority"),
        ("expires_at", NOW),
        ("issued_at", NOW + timedelta(seconds=1)),
    ]:
        old = getattr(cert, field)
        setattr(cert, field, bad)
        db.commit()
        assert database_policy(db, NOW).active_device_ids == ()
        setattr(cert, field, old)
    db.get(Repeater, A).cert_serial = "ab"
    db.commit()
    assert database_policy(db, NOW).active_device_ids == ()


def test_malformed_serial_fails(db):
    db.get(DeviceCredential, A).cert_serial = "0xFF"
    db.commit()
    with pytest.raises(ValueError):
        database_policy(db, NOW)


@pytest.mark.parametrize("status", ["adopted", "connected", "offline"])
def test_all_allowed_statuses(db, status):
    db.get(Repeater, A).status = status
    db.commit()
    assert database_policy(db, NOW).active_device_ids == (A,)


def test_missing_certificate_and_bounds(db, monkeypatch):
    monkeypatch.setattr(publication, "MAX_ACTIVE_DEVICES", 0)
    with pytest.raises(ValueError, match="too many"):
        database_policy(db, NOW)
    monkeypatch.setattr(publication, "MAX_ACTIVE_DEVICES", 1024)
    db.get(DeviceCredential, A).cert_serial = "ab"
    db.commit()
    assert database_policy(db, NOW).active_device_ids == ()
    cert = db.query(Certificate).one()
    cert.revoked_at = NOW
    db.commit()
    monkeypatch.setattr(publication, "MAX_REVOKED_CERTIFICATES", 0)
    with pytest.raises(ValueError, match="too many"):
        database_policy(db, NOW)


def test_old_serial_revocation_and_credential_revokes_all(db):
    db.add(
        Certificate(
            repeater_id=A,
            serial="aa",
            cn="device:" + A,
            issued_at=NOW - timedelta(days=2),
            expires_at=NOW + timedelta(days=1),
            revoked_at=NOW,
        )
    )
    db.commit()
    assert database_policy(db, NOW).active_device_ids == (A,)
    assert database_policy(db, NOW).revoked == (("aa", NOW),)
    db.get(DeviceCredential, A).revoked_at = NOW
    db.commit()
    assert database_policy(db, NOW).revoked == (("aa", NOW), ("ff", NOW))


@pytest.fixture
def pki(tmp_path, monkeypatch):
    # Unprivileged test host: exercise actual modes/atomic files in our own
    # group. Production fixed GID1883 provisioning needs parent image proof.
    monkeypatch.setattr(publication, "BROKER_GID", os.getgid())
    service = PkiService(Settings(pki_state_dir=str(tmp_path)))
    service.ensure_ca()
    service.ensure_mqtt_broker_server_certificate()
    service.ensure_backend_mqtt_client_certificate()
    service.ensure_broker_assets()
    return service


def test_assets_empty_bootstrap_and_ca_preserved(pki):
    root = pki.broker_directory
    before = (root.parent / "ca.key.pem").read_bytes()
    assert {p.name for p in root.iterdir()} == {
        "ca.crt.pem",
        "mqtt-broker.crt.pem",
        "mqtt-broker.key.pem",
        "current",
        (root / "current").resolve().name,
    }
    assert root.stat().st_mode & 0o777 == 0o750
    assert (root / "mqtt-broker.key.pem").stat().st_mode & 0o777 == 0o640
    assert (root / "ca.crt.pem").stat().st_mode & 0o777 == 0o644
    assert (root / "current/acl").read_text() == publication.build_acl([])
    ca, _ = pki.broker_signing_material()
    crl = x509.load_pem_x509_crl((root / "current/crl.pem").read_bytes())
    assert len(crl) == 0 and crl.is_signature_valid(ca.public_key())
    pki.ensure_broker_assets()
    assert before == (root.parent / "ca.key.pem").read_bytes()


def test_cache_acl_change_does_not_resign_and_crl_refresh(pki):
    pub = publication.BrokerPolicyPublisher(pki)
    now = datetime.now(UTC)
    empty = publication.PolicySnapshot((), ())
    assert pub.publish(empty, now) is False
    assert pub.publish(empty, now + timedelta(minutes=5)) is False
    original = (pub.root / "current/crl.pem").read_bytes()
    assert pub.publish(publication.PolicySnapshot((A,), ()), now)
    assert (pub.root / "current/crl.pem").read_bytes() == original
    assert not publication.BrokerPolicyPublisher(pki).publish(
        publication.PolicySnapshot((A,), ()), now
    )
    assert pub.publish(publication.PolicySnapshot((A,), ()), now + timedelta(days=7, minutes=-30))
    assert (pub.root / "current/crl.pem").read_bytes() != original
    assert len(list(pub.root.glob("policy-*"))) <= 2


@pytest.mark.parametrize("fault", ["stage", "replace"])
def test_publication_failure_preserves_last_good(pki, monkeypatch, fault):
    pub = publication.BrokerPolicyPublisher(pki)
    before = (pub.root / "current").resolve()

    def fail(*args, **kwargs):
        raise OSError("injected")

    if fault == "stage":
        monkeypatch.setattr(publication, "stage_file", fail)
    else:
        monkeypatch.setattr(publication.os, "replace", fail)
    with pytest.raises(OSError):
        pub.publish(publication.PolicySnapshot((A,), ()), datetime.now(UTC))
    assert (pub.root / "current").resolve() == before
    assert len(list(pub.root.glob("policy-*"))) == 1


def test_invalid_policy_preserves_files(pki):
    pub = publication.BrokerPolicyPublisher(pki)
    before = (pub.root / "current").resolve()
    with pytest.raises(ValueError):
        pub.publish(publication.PolicySnapshot(("bad",), ()), datetime.now(UTC))
    assert (pub.root / "current").resolve() == before


def test_permissions_fail_honestly(tmp_path, monkeypatch):
    def denied(*args):
        raise PermissionError("denied")

    monkeypatch.setattr(publication.os, "chown", denied)
    with pytest.raises(PermissionError):
        publication.broker_permissions(tmp_path, 0o750)


def test_service_safe_error_observable(pki, caplog):
    def fail():
        raise RuntimeError("SECRET postgres://password")

    service = publication.BrokerPolicyService(Settings(), fail, pki)
    service.refresh()
    assert service.last_error == "RuntimeError"
    assert service.last_publication_at is None
    assert "SECRET" not in caplog.text and "unconfirmed" in caplog.text


def test_future_revocation_fails(db):
    db.query(Certificate).one().revoked_at = NOW + timedelta(seconds=1)
    db.commit()
    with pytest.raises(ValueError, match="future"):
        database_policy(db, NOW)


def test_malformed_device_id_fails(db):
    from sqlalchemy import update

    for model, field in (
        (Repeater, "id"),
        (DeviceCredential, "repeater_id"),
        (Certificate, "repeater_id"),
    ):
        db.execute(update(model).values({field: "bad"}))
    db.commit()
    with pytest.raises(ValueError):
        database_policy(db, NOW)


def test_real_revocation_publication_and_cache(pki):
    pub = publication.BrokerPolicyPublisher(pki)
    now = datetime.now(UTC).replace(microsecond=0)
    snapshot = publication.PolicySnapshot((), (("ff", now),))
    assert pub.publish(snapshot, now)
    crl = x509.load_pem_x509_crl((pub.root / "current/crl.pem").read_bytes())
    ca, _ = pki.broker_signing_material()
    assert crl.is_signature_valid(ca.public_key())
    assert crl.get_revoked_certificate_by_serial_number(255) is not None
    assert not pub.publish(snapshot, now + timedelta(minutes=5))


def test_post_replace_uncertainty_uses_actual_files(pki, monkeypatch):
    pub = publication.BrokerPolicyPublisher(pki)
    previous = (pub.root / "current").resolve()
    sync = publication.sync_directory

    def fail_root(path):
        if path == pub.root:
            raise OSError("durability uncertain")
        sync(path)

    monkeypatch.setattr(publication, "sync_directory", fail_root)
    policy = publication.PolicySnapshot((A,), ())
    with pytest.raises(OSError):
        pub.publish(policy, datetime.now(UTC))
    actual = (pub.root / "current").resolve()
    assert actual != previous and previous.exists()
    assert (actual / "acl").read_text() == publication.build_acl((A,))
    monkeypatch.setattr(publication, "sync_directory", sync)
    assert not pub.publish(policy, datetime.now(UTC))
    assert (pub.root / "current").resolve() == actual


def test_server_rotation_copies_only_broker_assets(pki):
    pki._settings.broker_policy_enabled = True
    root = pki.broker_directory
    original = (root / "mqtt-broker.key.pem").read_bytes()
    ca = (root / "ca.crt.pem").read_bytes()
    assert pki.ensure_mqtt_broker_server_certificate(extra_san_hosts=["candidate.invalid"])
    assert (root / "mqtt-broker.key.pem").read_bytes() != original
    assert (root / "mqtt-broker.key.pem").read_bytes() == (
        root.parent / "mqtt-broker.key.pem"
    ).read_bytes()
    assert (root / "ca.crt.pem").read_bytes() == ca
    assert not (root / "ca.key.pem").exists()
    assert not (root / "mqtt-backend-client.key.pem").exists()


def test_pki_init_preserves_existing_policy_and_ca(pki, monkeypatch):
    from app.scripts import pki_init

    pub = publication.BrokerPolicyPublisher(pki)
    pub.publish(publication.PolicySnapshot((A,), ()), datetime.now(UTC))
    ca_before = (pki.broker_directory.parent / "ca.key.pem").read_bytes()
    policy_before = (pki.broker_directory / "current").resolve()
    pki._settings.broker_policy_enabled = True
    monkeypatch.setattr(pki_init, "get_settings", lambda: pki._settings)
    pki_init.main()
    assert (pki.broker_directory.parent / "ca.key.pem").read_bytes() == ca_before
    assert (pki.broker_directory / "current").resolve() == policy_before
    assert (pki.broker_directory / "current/acl").read_text() == publication.build_acl((A,))


def test_worker_disabled_single_start_and_stop(pki):
    import asyncio

    async def run():
        settings = Settings()
        service = publication.BrokerPolicyService(settings, None, pki)
        calls = []
        service.refresh = lambda: calls.append(True)
        await service.start()
        assert service.task is None and not calls
        settings.broker_policy_enabled = True
        await service.start()
        task = service.task
        await service.start()
        assert service.task is task and calls == [True]
        await service.stop()
        assert task.done() and service.task is None

    asyncio.run(run())


def test_enabled_api_lifespan_policy_worker(tmp_path, monkeypatch):
    from app.config import get_settings
    from app.db.session import reset_db_caches
    from app.main import create_app
    from fastapi.testclient import TestClient

    monkeypatch.setattr(publication, "BROKER_GID", os.getgid())
    for key, value in {
        "DATABASE_URL": f"sqlite+pysqlite:///{tmp_path / 'api.db'}",
        "PKI_STATE_DIR": str(tmp_path / "pki"),
        "APP_ENV": "test",
        "BROKER_POLICY_ENABLED": "true",
        "MQTT_INGEST_ENABLED": "false",
        "ALERT_POLICY_MONITOR_ENABLED": "false",
        "ALERT_ACTION_DISPATCHER_ENABLED": "false",
        "BOOTSTRAP_SEED_ADMIN_ENABLED": "false",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    reset_db_caches()
    try:
        app = create_app()
        with TestClient(app):
            service = app.state.broker_policy_service
            assert service.task is not None
            assert service.last_error is None and service.last_publication_at is not None
            assert (tmp_path / "pki/broker/current/acl").read_text() == publication.build_acl(())
        assert service.task is None
    finally:
        get_settings.cache_clear()
        reset_db_caches()


def test_source_compose_mount_and_lifecycle_contracts():
    root = Path(__file__).resolve().parents[2]
    import yaml

    services = yaml.safe_load((root / "docker-compose.yml").read_text())["services"]
    broker = services["mosquitto"]
    assert "./pki/broker:/mosquitto/pki:ro" in broker["volumes"]
    assert not any(v.startswith("./pki:") for v in broker["volumes"])
    assert broker["healthcheck"]["test"] == ["CMD", "/usr/local/bin/glass-broker-healthcheck"]
    assert "mosquitto" not in services["backend"]["depends_on"]
    for name in ("pki-init", "backend"):
        assert services[name]["environment"]["BROKER_POLICY_ENABLED"] == "true"
    assert broker["depends_on"]["pki-init"]["condition"] == "service_completed_successfully"
    dockerfile = (root / "mosquitto/Dockerfile").read_text()
    assert "USER 1883:1883" in dockerfile
    assert (
        "crlfile /mosquitto/pki/current/crl.pem" in (root / "mosquitto/mosquitto.conf").read_text()
    )
    assert Settings().broker_policy_enabled is False
