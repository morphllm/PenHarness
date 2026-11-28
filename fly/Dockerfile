# syntax=docker/dockerfile:1
# Image for both the long-lived coordinator server (fly.toml) and the ephemeral
# worker machines that fleet.py creates via the Fly Machines API.
FROM node:22-bookworm-slim

ARG OPENCODE_VERSION=1.19.3
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl dnsmasq-base git iproute2 iptables jq nftables python3 python3-venv ripgrep tinyproxy util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --no-create-home --shell /usr/sbin/nologin hackme-dns \
    && npm install -g "opencode-ai@${OPENCODE_VERSION}" \
    && npm cache clean --force

COPY requirements.txt /tmp/harness-requirements.txt
RUN python3 -m venv /opt/penharness-venv \
    && /opt/penharness-venv/bin/pip install --no-cache-dir -r /tmp/harness-requirements.txt

# The harness is baked into the image: AGENTS.md + skills are the agent's
# standing instructions on every machine, .opencode/plugins holds the hunt
# loop. entrypoint copies them into the working directory so edits never
# touch the image copy.
COPY AGENTS.md CLAUDE.md fleet.py /opt/harness/
COPY engagement /opt/harness/engagement
COPY skills /opt/harness/skills
COPY .opencode /opt/harness/.opencode
COPY fly/opencode.json /opt/harness/fly/opencode.json
# Bake everything opencode would otherwise fetch at boot: the plugin SDK it installs into each
# config directory (~/.config/opencode and the project's .opencode) and the models.dev catalogue.
# With OPENCODE_DISABLE_MODELS_FETCH and OPENCODE_MODELS_PATH below, a worker boot makes no
# registry.npmjs.org or models.opencode.ai request (verified from the worker DNS log).
RUN for dir in /opt/harness/.opencode /home/node/.config/opencode; do \
        mkdir -p "$dir" \
        && printf '{\n  "dependencies": {\n    "@opencode-ai/plugin": "%s"\n  }\n}\n' "$OPENCODE_VERSION" > "$dir/package.json" \
        && (cd "$dir" && npm install --omit=dev --no-audit --no-fund --loglevel=error) || exit 1; \
    done \
    && curl -fsSL --retry 3 https://models.opencode.ai/api.json -o /opt/harness/opencode-models.json \
    && python3 -c 'import json; json.load(open("/opt/harness/opencode-models.json"))' \
    && npm cache clean --force \
    && chown -R node:node /home/node
COPY fly/entrypoint.sh /usr/local/bin/harness-entrypoint
COPY fly/paid-worker-entrypoint.sh /usr/local/bin/paid-worker-entrypoint
COPY fly/paid-worker-firewall.sh /usr/local/bin/paid-worker-firewall
COPY fly/paid-worker-preflight.sh /usr/local/bin/paid-worker-preflight
COPY fly/broker-smoke.py /usr/local/bin/broker-smoke
RUN chmod 0755 /usr/local/bin/harness-entrypoint /usr/local/bin/paid-worker-entrypoint /usr/local/bin/paid-worker-firewall /usr/local/bin/paid-worker-preflight /usr/local/bin/broker-smoke

ENV OPENCODE_CONFIG=/opt/harness/fly/opencode.json \
    OPENCODE_DISABLE_MODELS_FETCH=1 \
    OPENCODE_MODELS_PATH=/opt/harness/opencode-models.json \
    OPENCODE_DISABLE_AUTOUPDATE=1 \
    OPENCODE_DISABLE_LSP_DOWNLOAD=1 \
    PORT=4096 \
    PYTHONPATH=/opt/harness \
    PATH=/opt/penharness-venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

RUN mkdir -p /data/engagements /work/hunt && chown -R node:node /data /work

ENV HOME=/home/node
WORKDIR /work/hunt
EXPOSE 4096

ENTRYPOINT ["paid-worker-entrypoint"]
