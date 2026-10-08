#!/bin/sh
set -eu
pid=
# Mosquitto writes the PID without a newline; read returns failure at EOF.
IFS= read -r pid < /mosquitto/runtime/mosquitto.pid || [ -n "$pid" ]
case "$pid" in ''|*[!0-9]*) exit 1;; esac
[ "$pid" -gt 1 ]
kill -0 "$pid"
# Liveness only; real TLS/ACL enforcement requires separate synthetic probes.
