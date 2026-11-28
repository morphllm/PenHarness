#!/usr/bin/env bash
# Verify the egress policy before any agent process starts: the public internet is reachable,
# while everything that protects Morph (6PN, other controller ports, the Machines API, metadata,
# private ranges, SMTP, direct resolver access) is refused. Every probe runs as the agent's user.
set -euo pipefail
run_node() { runuser -u node --preserve-environment -- "$@"; }
curl_node() { run_node curl -sS --max-time 10 --noproxy '*' "$@"; }
fail() { printf 'preflight: %s (refusing to start agent)\n' "$1" >&2; exit 1; }

# tcp_refused HOST PORT: the connection must fail locally (nft reject), not succeed.
tcp_refused() {
    run_node /usr/bin/python3 - "$1" "$2" <<'PY'
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
family = socket.AF_INET6 if ":" in host else socket.AF_INET
s = socket.socket(family, socket.SOCK_STREAM)
s.settimeout(5)
try:
    s.connect((host, port))
except OSError:
    sys.exit(0)
sys.exit(1)
PY
}

nft list chain inet hackme_egress input >/dev/null || fail "inbound filter missing"
# The agent's user must not read the kernel log, and no helper daemon may hold a secret.
if run_node dmesg >/dev/null 2>&1; then fail "kernel log is readable by the agent"; fi
for pidfile in /tmp/hack-me-dnsmasq.pid /tmp/hack-me-tinyproxy.pid; do
    pid=$(cat "$pidfile" 2>/dev/null || true)
    [ -n "$pid" ] || continue
    if tr '\0' '\n' < "/proc/$pid/environ" | grep -Eq '^(HACK_ME_BROKER_TOKEN|OPENCODE_SERVER_PASSWORD|FLY_API_TOKEN)='; then
        fail "helper daemon $pid holds a secret in its environment"
    fi
done
printf 'preflight: inbound=controller-only\n'

printf 'preflight: target-dns=checking\n'
run_node getent ahosts "${HACK_ME_TARGET_HOSTNAME}" >/dev/null

printf 'preflight: controller-broker=checking\n'
code=$(curl_node -o /dev/null -w '%{http_code}' -X POST -H 'content-type: application/json' \
    -d '{}' "${HACK_ME_MODEL_BROKER_URL}/chat/completions" || true)
# 401 = the broker answered and refused a request without the engagement token.
[ "$code" = "401" ] || fail "controller broker unreachable or unauthenticated (http=$code)"
printf 'preflight: controller-broker=ok\n'

for port in 8080 22 4096; do
    tcp_refused "${HACK_ME_CONTROL_PRIVATE_IP}" "$port" || fail "controller port $port is reachable"
done
printf 'preflight: controller-other-ports=blocked\n'
tcp_refused fdaa::3 4280 || fail "Machines API (_api.internal:4280) is reachable"
printf 'preflight: machines-api=blocked\n'
for address in 169.254.169.254 fd00:ec2::254 10.0.0.1 172.16.0.1 192.168.0.1 100.64.0.1 fdaa::2; do
    tcp_refused "$address" 80 || fail "$address is reachable"
done
printf 'preflight: metadata-and-private=blocked\n'
first_ip=$(head -n 1 /tmp/hack-me-target-public-ips)
tcp_refused "$first_ip" 25 || fail "outbound SMTP is reachable"
printf 'preflight: smtp=blocked\n'
if run_node /usr/bin/python3 - <<'PY'
import socket, sys
s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
s.settimeout(3)
query = b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x07example\x03com\x00\x00\x01\x00\x01"
try:
    s.sendto(query, ("fdaa::3", 53))
    s.recvfrom(512)
except OSError:
    sys.exit(1)
PY
then
    fail "the agent can query the upstream resolver directly (bypasses the DNS log)"
fi
if run_node getent hosts _api.internal >/dev/null; then
    fail "*.internal names resolve for the agent"
fi
printf 'preflight: dns=logged-resolver-only\n'

if ! curl_node --fail --head --output /dev/null https://example.com; then
    fail "public internet is unreachable"
fi
printf 'preflight: public-internet=reachable\n'
if ! curl_node --fail --head --output /dev/null "https://${HACK_ME_TARGET_HOSTNAME}"; then
    fail "authorized target unreachable"
fi
if ! run_node curl -sS --max-time 15 --proxy "${HACK_ME_EGRESS_PROXY_URL}" --fail --head \
    --output /dev/null "https://${HACK_ME_TARGET_HOSTNAME}"; then
    echo 'preflight: authorized-target proxy failure; diagnostics follow' >&2
    tail -n 40 /var/log/hack-me/proxy.log >&2 || true
    exit 1
fi
printf 'preflight: authorized-target=reachable\n'
code=$(run_node curl -sS --max-time 10 --proxy "${HACK_ME_EGRESS_PROXY_URL}" -o /dev/null \
    -w '%{http_code}' http://169.254.169.254/ 2>/dev/null || true)
[ "$code" != "200" ] || fail "metadata reachable through the proxy"
printf 'preflight: proxy-metadata=refused\n'

# Optional one-model-call proof used only during the isolated preflight.
if [ "${HACK_ME_BROKER_SMOKE:-0}" = "1" ]; then
    /opt/penharness-venv/bin/python3 /usr/local/bin/broker-smoke
fi
