#!/usr/bin/env bash
# Paid-worker egress: default ALLOW to the public internet, with deny rules that protect Morph:
#   - Fly 6PN (fdaa::/16, inside fc00::/7) and *.internal names, except the controller's broker
#     port 8081 and the resolver's port 53 (only for the logging resolver's uid and root);
#   - every other controller port, _api.internal (Machines API on fdaa::3:4280), fdaa::2/::3 etc.;
#   - cloud metadata (169.254.0.0/16, fd00:ec2::254 is inside fc00::/7);
#   - private, CGNAT, loopback-off-lo, link-local, multicast and NAT64/6to4 ranges;
#   - outbound TCP 25.
# One nftables `inet` table enforces both families, so IPv4 and IPv6 cannot drift apart.
# Every DNS query (dnsmasq), every new outbound connection and every denied attempt (nft log ->
# /dev/kmsg) is written to /var/log/hack-me and mirrored to the machine's stdout (Fly logs), which
# the agent cannot rewrite. fleet.py harvest copies them back to the controller per worker
# (remote/<worker>/egress-*.log beside the run's findings).
# Fly's kernel has no xt_LOG/xt_owner, so this uses native nft (meta skuid, log), not iptables.
set -euo pipefail
[ "$(id -u)" = "0" ] || { echo "paid firewall needs root" >&2; exit 1; }
[ -n "${HACK_ME_TARGET_HOSTNAME:-}" ] || { echo "target hostname is required" >&2; exit 1; }
[ "${HACK_ME_EGRESS_ENFORCED:-0}" = "1" ] || { echo "enforced egress is required" >&2; exit 1; }
[ "${HACK_ME_EGRESS_PROXY_URL:-}" = "http://127.0.0.1:3128" ] || {
    echo "only the loopback egress proxy is permitted" >&2; exit 1;
}
case "${HACK_ME_CONTROL_PRIVATE_IP:-}" in
    fdaa:*) ;;
    *) echo "controller must have a private Fly6PN address" >&2; exit 1 ;;
esac
# Per-worker upload cap (tc tbf on the egress interface). Override with e.g. 20mbit; "off" disables.
EGRESS_RATE="${HACK_ME_EGRESS_RATE:-50mbit}"
# Inbound `fly ssh` (hallpass, tcp/22) is off by default: no machine in the org can open 22.
# For a debugging session set HACK_ME_WORKER_SSH_FROM to the operator's flyctl WireGuard peer
# address(es) (comma separated, from ~/.fly/config.yml `peerip`); only those may connect.
SSH_FROM="${HACK_ME_WORKER_SSH_FROM:-}"
if [ -n "$SSH_FROM" ]; then
    /opt/penharness-venv/bin/python3 - "$SSH_FROM" <<'PYSSH' || { echo "HACK_ME_WORKER_SSH_FROM must list fdaa: addresses" >&2; exit 1; }
import ipaddress, sys
for value in sys.argv[1].split(","):
    if ipaddress.ip_network(value.strip()).subnet_of(ipaddress.ip_network("fdaa::/16")) is not True:
        raise SystemExit(1)
PYSSH
fi
LOG_DIR=/var/log/hack-me
DNS_USER=hackme-dns

public_ips=$(/opt/penharness-venv/bin/python3 - <<'PYRESOLVE'
import ipaddress, os, socket
host = os.environ["HACK_ME_TARGET_HOSTNAME"]
rows = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
addresses = sorted({ipaddress.ip_address(row[4][0]).compressed for row in rows if ipaddress.ip_address(row[4][0]).is_global})
if not addresses:
    raise SystemExit("target has only non-public addresses")
print(*addresses, sep="\n")
PYRESOLVE
)
printf '%s\n' "$public_ips" > /tmp/hack-me-target-public-ips
# The control-plane address is supplied by actual VM provisioning, not X-Forwarded-For.
broker_host=$(/opt/penharness-venv/bin/python3 - <<'PYHOST'
import os, re
from urllib.parse import urlsplit
url = urlsplit(os.environ.get("HACK_ME_MODEL_BROKER_URL", ""))
host = url.hostname
if (
    url.scheme != "http" or url.port != 8081 or url.username or url.password
    or url.query or url.fragment or not host or not re.fullmatch(
        r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.internal", host
    )
):
    raise SystemExit("private broker hostname is not canonical")
print(host)
PYHOST
)
# Pin the target's validated public addresses and the broker's private address. The broker is
# the only *.internal name a worker can resolve (the local resolver refuses the rest).
grep -Fvw "$HACK_ME_TARGET_HOSTNAME" /etc/hosts \
    > /tmp/hack-me-host.map || true
for address in $public_ips; do
    printf '%s\t%s\n' "$address" "$HACK_ME_TARGET_HOSTNAME" >> /tmp/hack-me-host.map
done
printf '%s\t%s\n' "$HACK_ME_CONTROL_PRIVATE_IP" "$broker_host" >> /tmp/hack-me-host.map
chmod 0644 /tmp/hack-me-host.map
cat /tmp/hack-me-host.map > /etc/hosts
if ! resolved_control=$(/usr/bin/getent ahosts "$broker_host"); then
    printf 'preflight: controller hostname unresolved\n' >&2
    exit 1
fi
if ! printf '%s\n' "$resolved_control" | grep -Fq "$HACK_ME_CONTROL_PRIVATE_IP"; then
    printf 'preflight: controller hostname matched unexpected address\n%s\n' "$resolved_control" >&2
    exit 1
fi
printf 'preflight: controller-private-address=matched\n'

# Upstream resolvers come from Fly's resolv.conf (fdaa::3). Only the dnsmasq uid and root may
# reach them: Fly's hallpass (root) resolves _orgcert.internal there directly for `fly ssh`.
upstream4=() upstream6=()
while read -r kind address _rest; do
    [ "$kind" = "nameserver" ] || continue
    case "$address" in
        127.*|::1) ;;
        *:*) upstream6+=("$address") ;;
        *) upstream4+=("$address") ;;
    esac
done < /etc/resolv.conf
[ "${#upstream4[@]}" -gt 0 ] || [ "${#upstream6[@]}" -gt 0 ] || {
    echo "no upstream nameserver in /etc/resolv.conf" >&2; exit 1;
}
join() { local IFS=,; printf '%s' "$*"; }

install -d -m 0755 -o root -g root "$LOG_DIR"
install -m 0644 -o root -g root /dev/null "$LOG_DIR/connections.log"
install -m 0644 -o "$DNS_USER" -g root /dev/null "$LOG_DIR/dns.log"
install -m 0644 -o tinyproxy -g root /dev/null "$LOG_DIR/proxy.log"

ruleset=/tmp/hack-me-egress.nft
{
    echo 'table inet hackme_egress'
    echo 'delete table inet hackme_egress'
    echo 'table inet hackme_egress {'
    echo '  set deny4 { type ipv4_addr; flags interval; elements = {'
    echo '    0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12,'
    echo '    192.0.0.0/24, 192.168.0.0/16, 198.18.0.0/15, 224.0.0.0/4, 240.0.0.0/4 } }'
    echo '  set deny6 { type ipv6_addr; flags interval; elements = {'
    echo '    ::/127, ::ffff:0:0/96, 64:ff9b::/96, 64:ff9b:1::/48, 2002::/16,'
    echo '    fc00::/7, fe80::/10, ff00::/8 } }'
    echo '  chain output {'
    echo '    type filter hook output priority filter; policy accept;'
    echo '    oif "lo" accept'
    echo '    ct state established,related accept'
    # IPv6 neighbor/router discovery to the fe80::1 gateway. Without it all IPv6 (6PN included)
    # stops working ~30 s after boot, once the gateway's neighbor entry needs revalidation.
    echo '    ip6 daddr { fe80::/10, ff02::/16 } icmpv6 type { nd-neighbor-solicit, nd-neighbor-advert, nd-router-solicit } accept'
    # Only the logging resolver reaches Fly's resolver. Root (hallpass, resolving
    # _orgcert.internal for `fly ssh`) is added only when SSH is enabled for this worker.
    dns_uids="\"$DNS_USER\""
    [ -z "$SSH_FROM" ] || dns_uids="\"$DNS_USER\", root"
    if [ "${#upstream6[@]}" -gt 0 ]; then
        echo "    ip6 daddr { $(join "${upstream6[@]}") } meta l4proto { tcp, udp } th dport 53 meta skuid { $dns_uids } accept"
    fi
    if [ "${#upstream4[@]}" -gt 0 ]; then
        echo "    ip daddr { $(join "${upstream4[@]}") } meta l4proto { tcp, udp } th dport 53 meta skuid { $dns_uids } accept"
    fi
    echo "    ip6 daddr $HACK_ME_CONTROL_PRIVATE_IP tcp dport 8081 accept"
    echo '    tcp dport 25 log prefix "hackme-deny smtp " reject with tcp reset'
    echo '    ip daddr @deny4 log prefix "hackme-deny private4 " reject with icmpx admin-prohibited'
    echo '    ip6 daddr @deny6 log prefix "hackme-deny private6 " reject with icmpx admin-prohibited'
    echo '    ct state new log prefix "hackme-conn "'
    echo '  }'
    # Inbound: the controller may open the opencode port; nothing else may open anything.
    # Paid workers have no Fly services, so there are no Fly proxy health checks to admit.
    echo '  chain input {'
    echo '    type filter hook input priority filter; policy accept;'
    echo '    iif "lo" accept'
    echo '    ct state established,related accept'
    echo '    ip6 saddr fe80::/10 icmpv6 type { nd-neighbor-solicit, nd-neighbor-advert, nd-router-advert, nd-redirect } accept'
    echo "    ip6 saddr $HACK_ME_CONTROL_PRIVATE_IP tcp dport ${PORT:-4096} accept"
    if [ -n "$SSH_FROM" ]; then
        echo "    ip6 saddr { $SSH_FROM } tcp dport 22 accept"
    fi
    echo '    ct state new log prefix "hackme-in-deny " drop'
    echo '    ct state invalid drop'
    echo '  }'
    echo '}'
} > "$ruleset"
nft -f "$ruleset"
# Retire any rules from the previous iptables-based policy so a default DROP cannot linger.
iptables -P OUTPUT ACCEPT 2>/dev/null && iptables -F OUTPUT 2>/dev/null || true
ip6tables -P OUTPUT ACCEPT 2>/dev/null && ip6tables -F OUTPUT 2>/dev/null || true

# Logging resolver: every query (and its client port) lands in dns.log. *.internal is answered
# locally (NXDOMAIN) so a worker cannot enumerate the org's apps or machines.
servers=()
for address in "${upstream6[@]}" "${upstream4[@]}"; do servers+=("--server=$address"); done
# Also local-only: Flycast names, and reverse lookups of 6PN (fdaa::/16) and private ranges,
# which would otherwise map org addresses back to app and machine names.
# The daemons start with an empty environment: they never hold the broker token or passwords.
env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin dnsmasq --no-resolv --no-hosts --no-poll "${servers[@]}" --local=/internal/ --local=/flycast/ \
    --local=/a.a.d.f.ip6.arpa/ --bogus-priv --stop-dns-rebind \
    --listen-address=127.0.0.1 --listen-address=::1 --bind-interfaces --port=53 \
    --user="$DNS_USER" --group=nogroup --pid-file=/tmp/hack-me-dnsmasq.pid \
    --cache-size=1000 --log-queries=extra --log-async=50 --log-facility="$LOG_DIR/dns.log"
# dnsmasq makes its log 0660; the harvest reads it as the agent's user (read-only).
chmod 0644 "$LOG_DIR/dns.log"
printf 'nameserver 127.0.0.1\noptions edns0 trust-ad\n' > /etc/resolv.conf

# The kernel ring buffer (nft log lines, boot messages) is for root only.
echo 1 > /proc/sys/kernel/dmesg_restrict   # procps (sysctl) is not in the image
# Connection log: nft log lines from the kernel ring buffer, timestamped, one per new flow.
setsid -f /bin/bash -c '
    exec </dev/kmsg
    while IFS= read -r line; do
        case "$line" in
            *hackme-*) printf "%(%Y-%m-%dT%H:%M:%SZ)T %s\n" -1 "${line#*;}" >> '"$LOG_DIR"'/connections.log ;;
        esac
    done' >/dev/null 2>&1
# Mirror every egress log to the machine console: Fly keeps it outside the agent's reach.
# (stdout here is the entrypoint's, i.e. the machine log.)
setsid -f /bin/bash -c 'exec tail -q -n +1 -F '"$LOG_DIR"'/connections.log '"$LOG_DIR"'/dns.log '"$LOG_DIR"'/proxy.log 2>/dev/null | sed -u "s/^/egress: /"'

if [ "$EGRESS_RATE" != "off" ]; then
    egress_dev=$(ip route show default | awk '{for (i = 1; i < NF; i++) if ($i == "dev") {print $(i + 1); exit}}')
    tc qdisc replace dev "${egress_dev:-eth0}" root tbf rate "$EGRESS_RATE" burst 256kb latency 200ms
fi

# The loopback proxy stays (the controller still hands workers HTTP(S)_PROXY) but now forwards to
# any public host. It refuses *.internal and literal private hosts; nft enforces the same by IP.
cat > /etc/tinyproxy/hack-me.conf <<EOF
User tinyproxy
Group tinyproxy
Port 3128
Listen 127.0.0.1
PidFile "/tmp/hack-me-tinyproxy.pid"
Timeout 600
DisableViaHeader Yes
LogLevel Connect
LogFile "$LOG_DIR/proxy.log"
Allow 127.0.0.1
Allow ::1
FilterType ere
FilterDefaultDeny No
Filter "/etc/tinyproxy/hack-me-filter"
EOF
cat > /etc/tinyproxy/hack-me-filter <<'EOF'
(^|\.)internal\.?$
^localhost\.?$
^\[?(::1|fe80:|fc|fd|ff)
^(0|10|127)\.
^169\.254\.
^172\.(1[6-9]|2[0-9]|3[01])\.
^192\.168\.
^100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.
EOF
TINYPROXY_BIN=$(command -v tinyproxy)
[ -n "$TINYPROXY_BIN" ] || { echo "tinyproxy unavailable" >&2; exit 1; }
env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin "$TINYPROXY_BIN" -c /etc/tinyproxy/hack-me.conf
# Hard links the harvest can read through the worker's project directory (opencode's file API
# will not follow a symlink out of it). Root-owned, so the agent cannot edit them; the
# authoritative record is still /var/log/hack-me and the machine log.
rm -rf /work/hunt/egress
install -d -m 0755 -o root -g root /work/hunt/egress
for log_name in connections.log dns.log proxy.log; do
    ln -f "$LOG_DIR/$log_name" "/work/hunt/egress/$log_name"
done
printf 'preflight: egress-firewall=ready rate=%s\n' "$EGRESS_RATE"
