from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from app.api.routes import bootstrap
from app.config import Settings
from app.db.migrate import apply_migrations
from app.db.models import User
from app.schemas.bootstrap import BootstrapAdminRequest
from app.services.bootstrap_seed import seed_default_admin_if_needed
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def sessions(tmp_path):
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'bootstrap.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    apply_migrations(engine)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture(params=[None, "false"], ids=["inherited-env", "seeding-disabled-env"])
def seed_settings(request, monkeypatch):
    if request.param is not None:
        monkeypatch.setenv("BOOTSTRAP_SEED_ADMIN_ENABLED", request.param)
    return Settings(
        bootstrap_seed_admin_enabled=True,
        bootstrap_seed_admin_email="seed@example.com",
        bootstrap_seed_admin_password="strong-test-password",
        auth_password_min_length=12,
    )


def payload(email="first@example.com"):
    return BootstrapAdminRequest(email=email, password="strong-test-password")


def counts(sessions):
    with sessions() as db:
        return (
            db.scalar(select(func.count()).select_from(User)),
            db.scalar(text("SELECT COUNT(*) FROM bootstrap_claim")),
        )


def test_success_and_claim_prevent_reopening(sessions):
    with sessions() as db:
        bootstrap.bootstrap_admin(payload(), db)
    assert counts(sessions) == (1, 1)
    with sessions() as db:
        db.execute(text("DELETE FROM users"))
        db.commit()
        assert bootstrap.bootstrap_status(db).needs_bootstrap is False
        with pytest.raises(HTTPException) as exc:
            bootstrap.bootstrap_admin(payload("second@example.com"), db)
        assert exc.value.status_code == 409
    assert counts(sessions) == (0, 1)


def test_invalid_password_does_not_claim(sessions, monkeypatch):
    monkeypatch.setattr(bootstrap, "get_settings", lambda: Settings(auth_password_min_length=30))
    with sessions() as db:
        with pytest.raises(HTTPException) as exc:
            bootstrap.bootstrap_admin(payload(), db)
        assert exc.value.status_code == 400
    assert counts(sessions) == (0, 0)


@pytest.mark.parametrize("failure", ["hash", "flush", "audit", "commit"])
def test_failure_rolls_back_claim_and_user(sessions, monkeypatch, failure):
    def fail(*args, **kwargs):
        raise RuntimeError("injected failure")

    with sessions() as db:
        if failure == "hash":
            monkeypatch.setattr(bootstrap, "hash_password", fail)
        elif failure == "flush":
            original_flush = db.flush

            def fail_user_flush(*args, **kwargs):
                if any(isinstance(obj, User) for obj in db.new):
                    fail()
                return original_flush(*args, **kwargs)

            monkeypatch.setattr(db, "flush", fail_user_flush)
        elif failure == "audit":
            monkeypatch.setattr(bootstrap, "write_audit_log", fail)
        else:
            monkeypatch.setattr(db, "commit", fail)
        with pytest.raises(RuntimeError, match="injected failure"):
            bootstrap.bootstrap_admin(payload(), db)
        # Read from the very same session: rollback must be explicit, not close-time.
        assert db.scalar(select(func.count()).select_from(User)) == 0
        assert db.scalar(text("SELECT COUNT(*) FROM bootstrap_claim")) == 0
    monkeypatch.undo()
    with sessions() as db:
        bootstrap.bootstrap_admin(payload(), db)
    assert counts(sessions) == (1, 1)


def test_existing_user_preserved_and_claim_rolled_back(sessions):
    with sessions() as db:
        user = User(email="existing@example.com", password_hash="unchanged", role="viewer")
        db.add(user)
        db.commit()
        with pytest.raises(HTTPException) as exc:
            bootstrap.bootstrap_admin(payload(), db)
        assert exc.value.status_code == 409
        assert db.get(User, user.id).password_hash == "unchanged"
        assert db.get(User, user.id).role == "viewer"
    assert counts(sessions) == (1, 0)


@pytest.mark.parametrize("seed_competes", [False, True])
def test_file_sqlite_concurrent_distinct_emails(sessions, seed_competes, seed_settings):
    barrier = Barrier(2)

    def run(index):
        barrier.wait(timeout=5)
        if index == 1 and seed_competes:
            seed_default_admin_if_needed(seed_settings, sessions)
            return "seed"
        with sessions() as db:
            try:
                bootstrap.bootstrap_admin(payload(f"admin{index}@example.com"), db)
                return 200
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert counts(sessions) == (1, 1)
    if not seed_competes:
        assert sorted(results) == [200, 409]
    else:
        assert results[0] in (200, 409)


def test_seed_does_not_reopen_or_replace_user(sessions, seed_settings):
    settings = seed_settings
    seed_default_admin_if_needed(settings, sessions)
    seed_default_admin_if_needed(settings, sessions)
    assert counts(sessions) == (1, 1)
    with sessions() as db:
        assert db.scalar(select(User.email)) == "seed@example.com"
        db.execute(text("DELETE FROM users"))
        db.commit()
    seed_default_admin_if_needed(settings, sessions)
    assert counts(sessions) == (0, 1)


@pytest.mark.parametrize("existing", [False, True])
def test_migration_backfills_existing_users_without_changing_them(tmp_path, existing):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'upgrade.db'}")
    apply_migrations(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE bootstrap_claim"))
        conn.execute(text("DELETE FROM schema_migrations WHERE version = '0017_bootstrap_claim'"))
        if existing:
            conn.execute(
                text(
                    "INSERT INTO users "
                    "(id,email,password_hash,role,is_active,created_at,updated_at) "
                    "VALUES ('old','old@example.com','preserved','viewer',1,"
                    "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                )
            )
    apply_migrations(engine)
    apply_migrations(engine)
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT COUNT(*) FROM bootstrap_claim")) == int(existing)
        if existing:
            assert conn.execute(text("SELECT password_hash,role FROM users")).one() == (
                "preserved",
                "viewer",
            )
    engine.dispose()
