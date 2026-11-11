# syntax=docker/dockerfile:1
# Image for both the long-lived coordinator server (fly.toml) and the ephemeral
# worker machines that fleet.py creates via the Fly Machines API.
FROM node:22-bookworm-slim

ARG OPENCODE_VERSION=1.19.3
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl git jq python3 ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g "opencode-ai@${OPENCODE_VERSION}" \
    && npm cache clean --force

# The harness is baked into the image: AGENTS.md + skills are the agent's
# standing instructions on every machine, .opencode/plugins holds the hunt
# loop. entrypoint copies them into the working directory so edits never
# touch the image copy.
COPY AGENTS.md CLAUDE.md /opt/harness/
COPY skills /opt/harness/skills
COPY .opencode /opt/harness/.opencode
COPY fly/opencode.json /opt/harness/fly/opencode.json
COPY fly/entrypoint.sh /usr/local/bin/harness-entrypoint
RUN chmod 0755 /usr/local/bin/harness-entrypoint

ENV OPENCODE_CONFIG=/opt/harness/fly/opencode.json \
    PORT=4096

USER node
ENV HOME=/home/node
WORKDIR /work/hunt
EXPOSE 4096

ENTRYPOINT ["harness-entrypoint"]
