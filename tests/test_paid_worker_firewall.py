"""Static checks on the paid-worker egress policy (fly/paid-worker-firewall.sh).

The live behavior is measured on Fly by sectest/probe.py; these pin the rules that protect Morph
so an edit cannot silently drop one, and keep IPv4 and IPv6 in step.
"""

import ipaddress
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIREWALL = (ROOT / "fly" / "paid-worker-firewall.sh").read_text()
PREFLIGHT = (ROOT / "fly" / "paid-worker-preflight.sh").read_text()


def _set(name: str) -> list:
    match = re.search(r"set %s \{.*?elements = \{(.*?)\} \}" % name, FIREWALL, re.S)
    assert match, name
    return [ipaddress.ip_network(value.strip()) for value in match.group(1).replace("'", "").replace("echo", "").split(",")
            if value.strip()]


def _covered(networks, address: str) -> bool:
    return any(ipaddress.ip_address(address) in network for network in networks)


class PaidWorkerFirewallTest(unittest.TestCase):
    def test_scripts_parse(self):
        for path in ("fly/paid-worker-firewall.sh", "fly/paid-worker-preflight.sh"):
            subprocess.run(["bash", "-n", str(ROOT / path)], check=True)

    def test_default_allow_with_one_inet_table(self):
        self.assertIn("table inet hackme_egress", FIREWALL)
        self.assertIn("policy accept;", FIREWALL)
        self.assertNotIn("-P OUTPUT DROP", FIREWALL)

    def test_private_metadata_and_fly_ranges_are_denied_in_both_families(self):
        deny4, deny6 = _set("deny4"), _set("deny6")
        for address in ("10.1.2.3", "172.19.0.1", "192.168.1.1", "100.64.0.1", "169.254.169.254",
                        "127.0.0.2", "0.0.0.1", "224.0.0.1"):
            self.assertTrue(_covered(deny4, address), address)
        for address in ("fdaa::3", "fdaa:11:f954::2", "fd00:ec2::254", "fc00::1", "fe80::1", "ff02::1",
                        "::1", "64:ff9b::a00:1", "::ffff:10.0.0.1", "2002:a00:1::1"):
            self.assertTrue(_covered(deny6, address), address)
        for address in ("1.1.1.1", "66.241.125.169"):
            self.assertFalse(_covered(deny4, address), address)
        for address in ("2606:4700:4700::1111", "2a09:8280:1::225:8e6:0"):
            self.assertFalse(_covered(deny6, address), address)

    def test_only_the_broker_port_on_the_controller_is_allowed(self):
        output = FIREWALL.split("chain output {", 1)[1].split("chain input {", 1)[0]
        allows = re.findall(r"HACK_ME_CONTROL_PRIVATE_IP[^\n]*accept", output)
        self.assertEqual(allows, ['HACK_ME_CONTROL_PRIVATE_IP tcp dport 8081 accept'])

    def test_resolver_is_reachable_only_by_the_logging_resolver_and_root(self):
        rules = [line for line in FIREWALL.splitlines() if "th dport 53" in line]
        self.assertEqual(len(rules), 2)
        for rule in rules:
            self.assertIn("meta skuid { $dns_uids } accept", rule)
        self.assertIn('dns_uids="\\"$DNS_USER\\""', FIREWALL)
        self.assertIn('[ -z "$SSH_FROM" ] || dns_uids=', FIREWALL)
        self.assertIn("--local=/internal/", FIREWALL)
        self.assertIn("--log-queries", FIREWALL)

    def test_smtp_ndp_logging_and_bandwidth(self):
        self.assertIn("tcp dport 25", FIREWALL)
        self.assertIn("nd-neighbor-solicit", FIREWALL)
        self.assertIn('ct state new log prefix "hackme-conn "', FIREWALL)
        self.assertIn("tc qdisc replace", FIREWALL)

    def test_inbound_admits_only_the_controller_and_optional_ssh_peers(self):
        chain = FIREWALL.split("chain input {", 1)[1]
        accepts = [line.strip() for line in chain.splitlines() if "accept" in line and "echo" in line and "policy" not in line]
        self.assertEqual(len(accepts), 5)
        self.assertTrue(any("HACK_ME_CONTROL_PRIVATE_IP tcp dport ${PORT:-4096} accept" in a for a in accepts))
        self.assertTrue(any("tcp dport 22 accept" in a for a in accepts))
        self.assertIn('if [ -n "$SSH_FROM" ]; then\n        echo "    ip6 saddr { $SSH_FROM } tcp dport 22 accept"', FIREWALL)
        self.assertIn('ct state new log prefix "hackme-in-deny " drop', chain)

    def test_daemons_start_without_secrets_and_dmesg_is_root_only(self):
        # dnsmasq and tinyproxy run as other uids; an empty env keeps the broker token out of them.
        self.assertRegex(FIREWALL, r"env -i PATH=\S+ dnsmasq ")
        self.assertRegex(FIREWALL, r'env -i PATH=\S+ "\$TINYPROXY_BIN"')
        self.assertIn("echo 1 > /proc/sys/kernel/dmesg_restrict", FIREWALL)

    def test_resolver_refuses_internal_flycast_and_6pn_reverse_names(self):
        for flag in ("--local=/internal/", "--local=/flycast/", "--local=/a.a.d.f.ip6.arpa/", "--bogus-priv"):
            self.assertIn(flag, FIREWALL)

    def test_preflight_asserts_the_protections(self):
        for needle in ("8080 22 4096", "fdaa::3 4280", "169.254.169.254", "fd00:ec2::254", "_api.internal",
                       "outbound SMTP", "public-internet=reachable"):
            self.assertIn(needle, PREFLIGHT)


if __name__ == "__main__":
    unittest.main()


class HeadlessOpencodePermissionTest(unittest.TestCase):
    """No human answers opencode prompts on a worker: an "ask" rule hangs the session until the
    run deadline. opencode defaults external_directory and doom_loop to ask, so the config must
    decide them (seen on Fly: `curl -D /tmp/h1` parked a paid run for 25 minutes)."""

    def test_every_prompting_permission_is_decided(self):
        import json
        permission = json.loads((ROOT / "fly" / "opencode.json").read_text())["permission"]
        self.assertEqual(permission["external_directory"], "allow")
        self.assertEqual(permission["doom_loop"], "allow")
        self.assertEqual(permission["question"], "deny")
        self.assertEqual(permission["plan_enter"], "deny")
        self.assertNotIn("ask", json.dumps(permission))
