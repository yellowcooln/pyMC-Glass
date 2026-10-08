#!/bin/sh
# Fixed broker-only supervisor. No command/path arguments or Docker API.
set -eu
child=
sleeper=
stop_child() {
    if [ -n "$child" ]; then
        kill -TERM "$child" 2>/dev/null || :
        n=0
        while kill -0 "$child" 2>/dev/null && [ "$n" -lt 10 ]; do
            sleep 1
            n=$((n + 1))
        done
        kill -KILL "$child" 2>/dev/null || :
        wait "$child" 2>/dev/null || :
        child=
    fi
    rm -f /mosquitto/runtime/mosquitto.pid
}
cleanup() {
    trap - EXIT TERM INT
    if [ -n "$sleeper" ]; then
        kill "$sleeper" 2>/dev/null || :
        wait "$sleeper" 2>/dev/null || :
    fi
    stop_child
}
trap cleanup EXIT
trap 'exit 0' TERM INT
acl_hash() { sha256sum /mosquitto/pki/current/acl; }
tls_hash() {
    sha256sum /mosquitto/pki/current/crl.pem /mosquitto/pki/ca.crt.pem /mosquitto/pki/mqtt-broker.crt.pem /mosquitto/pki/mqtt-broker.key.pem
}
acl=$(acl_hash)
tls=$(tls_hash)
start_child() {
    /usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf &
    child=$!
}
start_child
while :; do
    sleep 1 &
    sleeper=$!
    wait "$sleeper"
    sleeper=
    if ! kill -0 "$child" 2>/dev/null; then
        status=0
        wait "$child" || status=$?
        child=
        # Even an unexpected clean child exit requires container restart.
        [ "$status" -ne 0 ] || status=1
        exit "$status"
    fi
    next_acl=$(acl_hash)
    next_tls=$(tls_hash)
    if [ "$next_tls" != "$tls" ]; then
        # HUP cannot evict an already authenticated revoked serial.
        stop_child
        start_child
        printf '%s\n' 'Broker TLS policy changed; all clients disconnected' >&2
    elif [ "$next_acl" != "$acl" ]; then
        kill -HUP "$child"
    fi
    acl=$next_acl
    tls=$next_tls
done
