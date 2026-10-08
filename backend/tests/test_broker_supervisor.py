"""Execute supervisor with a fixed fake child; never start a real broker."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def eventually(predicate):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("fake supervisor condition timed out")


@pytest.fixture
def supervised(tmp_path):
    tree = tmp_path / "mosquitto"
    (tree / "pki/current").mkdir(parents=True)
    (tree / "runtime").mkdir()
    for name in (
        "current/acl",
        "current/crl.pem",
        "ca.crt.pem",
        "mqtt-broker.crt.pem",
        "mqtt-broker.key.pem",
    ):
        (tree / "pki" / name).write_text("initial")
    events = tmp_path / "events"
    fake = tmp_path / "fixed-fake-child"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import os, signal, time\n"
        f"events = {str(events)!r}\n"
        "def event(kind):\n"
        "    with open(events, 'a') as f: f.write(kind + ':' + str(os.getpid()) + '\\n')\n"
        "def term(*args):\n"
        "    event('TERM')\n"
        "    raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM, term)\n"
        "signal.signal(signal.SIGHUP, lambda *a: event('HUP'))\n"
        f"with open({str(tree / 'runtime/mosquitto.pid')!r}, 'w') as f: f.write(str(os.getpid()))\n"
        "event('START')\n"
        "while True: time.sleep(.05)\n"
    )
    fake.chmod(0o700)
    # Test-only substitution of fixed paths, NOT a production override/API.
    script = (
        (ROOT / "mosquitto/supervise.sh")
        .read_text()
        .replace("/usr/sbin/mosquitto", str(fake))
        .replace("/mosquitto/", str(tree) + "/")
    )
    wrapper = tmp_path / "supervise.sh"
    wrapper.write_text(script)
    proc = subprocess.Popen(["sh", str(wrapper)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def lines():
        return events.read_text().splitlines() if events.exists() else []

    try:
        eventually(lambda: len(lines()) == 1)
        yield proc, tree, lines
    finally:
        if proc.poll() is None:
            proc.terminate()
        proc.communicate(timeout=15)
        for row in lines():
            if row.startswith("START:"):
                pid = int(row.split(":")[1])
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    continue
                os.kill(pid, signal.SIGKILL)
                raise AssertionError("supervisor leaked fake child")


@pytest.fixture
def healthcheck(tmp_path):
    tree = tmp_path / "mosquitto"
    (tree / "runtime").mkdir(parents=True)
    script = (ROOT / "mosquitto/healthcheck.sh").read_text().replace("/mosquitto/", str(tree) + "/")
    wrapper = tmp_path / "healthcheck.sh"
    wrapper.write_text(script)

    def run(content):
        pidfile = tree / "runtime/mosquitto.pid"
        if content is not None:
            pidfile.write_text(content)
        return subprocess.run(["sh", str(wrapper)], capture_output=True, text=True, timeout=5)

    return run


@pytest.mark.parametrize("ending", ["", "\n"], ids=["no-newline", "newline"])
def test_healthcheck_accepts_live_pid(healthcheck, ending):
    result = healthcheck(str(os.getpid()) + ending)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "content",
    [None, "", "\n", "not-a-pid", "123abc\n", "0", "1", "-1", " 2", "2 "],
    ids=[
        "missing",
        "empty",
        "blank",
        "nonnumeric",
        "mixed",
        "zero",
        "init",
        "negative",
        "leading-space",
        "trailing-space",
    ],
)
def test_healthcheck_rejects_invalid_pid(healthcheck, content):
    assert healthcheck(content).returncode != 0


@pytest.mark.parametrize("ending", ["", "\n"], ids=["no-newline", "newline"])
def test_healthcheck_rejects_dead_pid(healthcheck, ending):
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=5)
    assert healthcheck(str(child.pid) + ending).returncode != 0


def test_acl_hup_crl_restart_and_term_cleanup(supervised):
    proc, tree, lines = supervised
    first = lines()[0].split(":")[1]
    (tree / "pki/current/acl").write_text("changed ACL")
    eventually(lambda: "HUP:" + first in lines())
    assert len([r for r in lines() if r.startswith("START:")]) == 1
    (tree / "pki/current/crl.pem").write_text("changed CRL")
    eventually(lambda: len([r for r in lines() if r.startswith("START:")]) == 2)
    assert "TERM:" + first in lines()
    second = [r for r in lines() if r.startswith("START:")][-1].split(":")[1]
    assert second != first
    proc.terminate()
    assert proc.wait(timeout=15) == 0
    assert "TERM:" + second in lines()
    assert not (tree / "runtime/mosquitto.pid").exists()


def test_unexpected_child_exit_fails_container(supervised):
    proc, tree, lines = supervised
    os.kill(int(lines()[0].split(":")[1]), signal.SIGTERM)
    assert proc.wait(timeout=8) == 1
    assert not (tree / "runtime/mosquitto.pid").exists()


def test_missing_new_policy_stops_child_no_fallback(supervised):
    proc, tree, lines = supervised
    (tree / "pki/current/acl").unlink()
    assert proc.wait(timeout=15) != 0
    assert any(row.startswith("TERM:") for row in lines())
    assert not (tree / "runtime/mosquitto.pid").exists()
