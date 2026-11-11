#!/usr/bin/env bash
# Attach a local opencode client to the long-lived coordinator server on Fly.
# Loads OPENCODE_SERVER_PASSWORD from the git-ignored .env automatically.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if [ -f "$root/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$root/.env"
    set +a
fi

: "${OPENCODE_SERVER_PASSWORD:?Set OPENCODE_SERVER_PASSWORD in the git-ignored .env}"
: "${FLY_APP:?Set FLY_APP in the git-ignored .env}"

exec opencode run \
    --attach "${OPENCODE_SERVER_URL:-https://$FLY_APP.fly.dev}" \
    --dir /work/hunt \
    "$@"
