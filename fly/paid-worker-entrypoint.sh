#!/usr/bin/env bash
set -euo pipefail
if [ "${PENHARNESS_CONTROL_PLANE:-0}" = "1" ]; then
    # A fresh Fly volume is root-owned; grant only the controller's node user its state directory.
    install -d -m 0700 -o node -g node /data/engagements
    chown -R node:node /data/engagements
fi
if [ "${HACK_ME_PAID_FIREWALL:-0}" = "1" ]; then
    /usr/local/bin/paid-worker-firewall
    /usr/local/bin/paid-worker-preflight
fi
exec runuser -u node --preserve-environment -- /usr/local/bin/harness-entrypoint "$@"
