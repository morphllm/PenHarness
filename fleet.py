#!/usr/bin/env python3
"""fleet.py — the entire fleet layer for PenHarness 2.

Creates ephemeral boxes running `opencode serve`, dispatches one fresh opencode
session per task over HTTP, steers long-lived lane-head sessions, and harvests
findings.d/ from every box. Stdlib only: no venv, no pip install.

Three backends (`--backend` or FLEET_BACKEND, default `local`):
  local  THE DEFAULT. No remote fleet: the lead agent runs workers as local
         subagents (native to opencode/Claude Code and friends). fleet.py `up`
         in local mode just prints the doctrine; there is nothing to boot.
         Zero extra setup; heats the machine up when swarms get big.
  boat   boat.dev sandboxes (the recommended upgrade). Each box gets its own
         stable token-gated HTTPS URL (https://<box>-4096.on.boat.dev) via
         `boat host`, bootstrapped over `boat exec` — no ssh, no wireguard, no
         image builds. Requires: `boat` CLI installed and logged in.
  fly    Fly.io machines from a customer-built image (fly deploy). Reached over
         private 6PN/wireguard, or the experimental fly-replay public path.
         Requires: FLY_API_TOKEN, FLY_APP, a deployed image.

Configuration comes from the environment (a .env file in the current
directory is loaded first):

  FLEET_BACKEND              boat | fly (default: boat)
  OPENCODE_SERVER_PASSWORD   basic-auth password baked into boxes (also used
                             by this CLI to authenticate to them)
  OPENCODE_SERVER_USERNAME   optional, default "opencode"
  MORPH_API_KEY              provider key injected into boxes on `up` (optional;
                             without it boxes boot but prompts fail at the provider)
  WORKER_MODEL               muscle model id (default: morph/morph-dsv41flash-1017)
  LANE_MODEL                 lane-head model id (default: morph/morph-kimik3-fast)
  BOAT_TYPE                  sandbox size (default: small)
  BOAT_TTL                   sandbox lifetime seconds (default: 14400)
  FLY_API_TOKEN / FLY_APP / FLY_REGION   fly backend only

Commands: up, list, status, warm, dispatch, lane, steer, harvest, down.
Run `python3 fleet.py <command> --help` for details. State lives in
fleet-state.json in the current directory — run from the hunt directory.
"""

import argparse
import base64
import concurrent.futures as cf
import io
import json
import os
import random
import string
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

MACHINES_API = "https://api.machines.dev/v1"
STATE_FILE = "fleet-state.json"
DEFAULT_REGION = "iad"
DEFAULT_BACKEND = "local"
DEFAULT_WORKER_MODEL = "morph-abliterated/morph-dsv41flash-abliterated"
DEFAULT_LANE_MODEL = "morph/morph-kimik3-fast"
OPENCODE_PORT = 4096
HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))


class FleetError(RuntimeError):
    """User-actionable error."""


def die(msg, code=1):
    print(f"fleet: error: {msg}", file=sys.stderr)
    sys.exit(code)


def load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        die(f"missing ${name} — set it in the environment or .env")
    return value


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"machines": [], "lanes": {}, "dispatched": []}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, STATE_FILE)


def rand_id(n=6):
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(n))


# --- HTTP helpers -----------------------------------------------------------


def _request(url, method="GET", body=None, headers=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise FleetError(f"{method} {url} -> {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise FleetError(
            f"{method} {url} failed: {e.reason}. If this is a machine address, "
            "check your Fly wireguard: fly wireguard status / fly wireguard up"
        )


def mapi(method, path, body=None, timeout=120):
    token = env("FLY_API_TOKEN", required=True)
    return _request(
        MACHINES_API + path,
        method=method,
        body=body,
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    )


def box_req(machine, method, path, body=None, timeout=3600):
    user = env("OPENCODE_SERVER_USERNAME", "opencode")
    token = base64.b64encode(
        f"{user}:{env('OPENCODE_SERVER_PASSWORD', required=True)}".encode()
    ).decode()
    headers = {"Authorization": f"Basic {token}"}
    backend = machine.get("backend", "fly")
    if backend == "boat":
        # Each boat sandbox has its own stable token-gated HTTPS URL. Send the
        # boat proxy token as a cookie (the ?_token= form 302s; API clients
        # must use the cookie form), plus opencode basic auth.
        url = machine["url"] + path
        headers["Cookie"] = f"_port_auth={machine['token']}"
        return _request(url, method=method, body=body, headers=headers, timeout=timeout)
    # fly backend: public anycast URL pinned to the machine via fly-replay
    # (experimental: proven flaky under parallel load), else private 6PN
    # address (requires wireguard on the lead).
    if machine.get("instance"):
        app = env("FLY_APP", required=True)
        url = f"https://{app}.fly.dev{path}"
        headers["fly-replay"] = f"instance={machine['instance']}"
    else:
        ip = machine.get("ip")
        if not ip:
            raise FleetError(f"machine {machine.get('name') or machine.get('id')} has no address")
        port = machine.get("port") or int(env("OPENCODE_PORT", OPENCODE_PORT))
        url = f"http://{ip}:{port}{path}"
    return _request(url, method=method, body=body, headers=headers, timeout=timeout)


# --- boat.dev backend -------------------------------------------------------


def boat_cmd(args, stdin=None, timeout=120):
    """Run the boat CLI; returns stdout. `boat` must be installed and logged in."""
    try:
        result = subprocess.run(
            ["boat", *args], input=stdin, text=True, capture_output=True, timeout=timeout
        )
    except FileNotFoundError:
        raise FleetError("boat CLI not found — install it (see https://boat.dev) and `boat login`")
    except subprocess.TimeoutExpired:
        raise FleetError(f"boat {' '.join(args)} timed out after {timeout}s")
    if result.returncode != 0:
        raise FleetError(f"boat {' '.join(args)} failed: {result.stderr.strip()[:300]}")
    return result.stdout


def boat_json(args, stdin=None, timeout=120):
    return json.loads(boat_cmd(args, stdin=stdin, timeout=timeout))


def harness_blob_b64():
    """tar.gz+base64 of the harness standing instructions (AGENTS.md, skills,
    plugin, opencode server config) baked into every boat box at bootstrap."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in ("AGENTS.md", "CLAUDE.md"):
            tar.add(os.path.join(HARNESS_DIR, name), arcname=name)
        tar.add(os.path.join(HARNESS_DIR, "skills"), arcname="skills")
        tar.add(os.path.join(HARNESS_DIR, ".opencode"), arcname=".opencode")
        tar.add(os.path.join(HARNESS_DIR, "fly", "opencode.json"),
                arcname="fly-opencode.json")
    return base64.b64encode(buf.getvalue()).decode()


BOOTSTRAP = r"""#!/bin/sh
set -u
cd "$HOME"
umask 077
mkdir -p "$HOME/.config/opencode" findings.d lanes logs evidence .opencode/plugins

# Secrets arrive inside the script (stdin is NOT echoed by boat exec; the
# remote command string IS — never put secrets in argv).
cat > "$HOME/.hunt-boot.env" <<'BOOT_EOF'
OPENCODE_SERVER_PASSWORD=%(pw)s
MORPH_API_KEY=%(morph)s
BOOT_EOF

printf '%%s' '%(blob)s' | base64 -d | tar xz 2>/dev/null || echo blob-extract-failed >&2
cp fly-opencode.json "$HOME/.config/opencode/opencode.json" 2>/dev/null || true
rm -f fly-opencode.json

[ "%(huntloop)s" = "1" ] && : > .hunt-loop || rm -f .hunt-loop

pkill -f '[o]pencode serve' 2>/dev/null || true
sleep 1
set -a; . "$HOME/.hunt-boot.env"; set +a
setsid nohup opencode serve --hostname 0.0.0.0 --port %(port)d >"$HOME/logs/serve.log" 2>&1 </dev/null &
echo bootstrapped
"""


def boat_create(args, role, i):
    """Create one boat sandbox; returns the machine record (not yet served)."""
    create_args = ["new", "--type", args.type, "--ttl", str(args.ttl),
                   "--no-env", "--json"]
    if args.from_snapshot:
        create_args += ["--from", args.from_snapshot]
    # `boat new --json` streams NDJSON progress events; the final line is the
    # full ready object.
    out = boat_cmd(create_args, timeout=180).strip().splitlines()
    box = json.loads(out[-1])
    sid = box["id"]
    return {"backend": "boat", "id": sid,
            "name": f"{args.prefix}-{sid}",
            "role": role,
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def boat_wait_ready(mc, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            info = boat_json(["info", mc["id"], "--json"], timeout=30)
            box = info.get("sandbox", info)
            if (box.get("state") in ("ready", "idle", "running")
                    and box.get("health") in (None, "ok")):
                return mc
        except (FleetError, json.JSONDecodeError):
            pass
        time.sleep(3)
    raise FleetError(f"boat box {mc['id']} not ready within {timeout}s")


def boat_bootstrap(mc, script):
    # `boat exec` ignores stdin (detached or not), so the script rides argv as
    # base64. Caveat: the exec JSON echoes the command string — the b64 lands
    # in box-local process logs. The embedded OPENCODE_SERVER_PASSWORD should
    # therefore be treated as box-scoped and rotated per hunt.
    b64 = base64.b64encode(script.encode()).decode()
    remote = f"echo '{b64}' | base64 -d > /tmp/hunt-boot.sh && sh /tmp/hunt-boot.sh"
    out = boat_cmd(["exec", mc["id"], "--", "sh", "-c", remote], timeout=180)
    try:
        result = json.loads(out)
        if not result.get("success"):
            raise FleetError(
                f"bootstrap exec failed on {mc['id']}: {result.get('stderr', out)[:200]}")
    except json.JSONDecodeError:
        pass  # pre-JSON CLI output; health check will catch failure
    return mc


def boat_expose(mc):
    host = boat_json(["host", mc["id"], str(OPENCODE_PORT), "--json"], timeout=60)
    url = host["url"]
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    token = (qs.get("_token") or [None])[0]
    if not token:
        raise FleetError(f"boat host returned no _token: {url}")
    mc["url"] = url.split("?")[0]
    mc["token"] = token
    return mc


def boat_destroy(mc):
    boat_cmd(["delete", mc["id"], "--yes", "--json"], timeout=60)
    return mc["name"]


def destroy_machine(mc):
    if mc.get("backend") == "boat":
        return boat_destroy(mc)
    app = env("FLY_APP", required=True)
    mapi("DELETE", f"/apps/{app}/machines/{mc['id']}?force=true", timeout=60)
    return mc["name"]


def parallel(fn, items, max_workers=16):
    """Run fn(item) over items concurrently; return a list of results/errors in
    the same order as items (items are not required to be hashable)."""
    items = list(items)
    results = [None] * len(items)
    with cf.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fn, item): idx for idx, item in enumerate(items)}
        for fut in cf.as_completed(futures):
            idx = futures[fut]
            try:
                results[idx] = fut.result()
            except BaseException as e:  # noqa: BLE001 - incl. die()->SystemExit
                results[idx] = e
    return results


# --- Machine lifecycle ------------------------------------------------------


def find_machine_image(app):
    """Reuse the image of an existing machine in the app, if one exists."""
    _, machines = mapi("GET", f"/apps/{app}/machines")
    for m in machines or []:
        image = (m.get("config") or {}).get("image")
        if image:
            return image
    return None


def resolve_image(app, args_image):
    if args_image:
        return args_image
    image = find_machine_image(app)
    if image:
        return image
    raise FleetError(
        "no --image given and the app has no machines to copy one from. "
        "Deploy the image first (fly deploy --build-only) or pass --image "
        "registry.fly.io/<app>:<tag>"
    )


def cmd_up_boat(args):
    pw = env("OPENCODE_SERVER_PASSWORD", required=True)
    morph_key = env("MORPH_API_KEY") or "placeholder-not-real"
    if morph_key == "placeholder-not-real":
        print("  warning: $MORPH_API_KEY not set — boxes boot but prompts will fail "
              "with provider errors", file=sys.stderr)
    state = load_state()
    blob = harness_blob_b64()
    role = args.role
    print(f"creating {args.count} boat.dev sandbox(es): "
          f"type={args.type} ttl={args.ttl}s role={role}")

    created = [r for r in parallel(lambda i: boat_create(args, role, i), range(args.count))]

    ok, failed = [], []
    for i, r in enumerate(created):
        if isinstance(r, BaseException):
            failed.append(i)
            print(f"  [{i}] create FAILED: {r}", file=sys.stderr)
        else:
            ok.append(r)
            print(f"  created {r['name']}")

    script = BOOTSTRAP % {
        "pw": pw,
        "morph": morph_key,
        "blob": blob,
        # Only the lead/coordinator loops — lanes and workers are reactive
        # (steered by the lead; they report via ~/mailbox.jsonl).
        "huntloop": "0",
        "port": OPENCODE_PORT,
    }

    def stage(mc):
        boat_wait_ready(mc)
        boat_bootstrap(mc, script)
        boat_expose(mc)
        return mc

    ready = []
    for r in parallel(stage, ok):
        if isinstance(r, BaseException):
            print(f"  stage FAILED: {r}", file=sys.stderr)
            continue
        ready.append(r)
        print(f"  staged {r['name']}")

    def wait_health(mc):
        deadline = time.time() + 240
        while time.time() < deadline:
            try:
                status, _ = box_req(mc, "GET", "/global/health", timeout=15)
                if status == 200:
                    return mc
            except FleetError:
                pass
            time.sleep(4)
        raise FleetError(f"{mc['name']} never answered /global/health")

    healthy = []
    for r in parallel(wait_health, ready):
        if isinstance(r, BaseException):
            print(f"  health FAILED: {r}", file=sys.stderr)
        else:
            healthy.append(r)
            print(f"  healthy {r['name']}")

    state["machines"].extend(healthy)
    save_state(state)
    print(f"up: {len(healthy)} box(es) ready")


def cmd_up(args):
    backend = args.backend or env("FLEET_BACKEND", DEFAULT_BACKEND)
    if backend == "local":
        print(
            "local mode: there is no remote fleet to boot.\n"
            "The lead agent runs workers as LOCAL SUBAGENTS — spawn many of them,\n"
            "one hypothesis each, model = morph-abliterated/morph-dsv41flash-abliterated\n"
            "when the subagent tool supports model selection; the same findings.d/\n"
            "serialization contract applies (write files, never just chat).\n"
            "When this machine gets hot and crowded, upgrade with:\n"
            "  boat login && python3 fleet.py up 20 --backend boat"
        )
        return
    if backend == "boat":
        args.type = args.type or env("BOAT_TYPE", "small")
        args.ttl = args.ttl or int(env("BOAT_TTL", "14400"))
        return cmd_up_boat(args)
    app = env("FLY_APP", required=True)
    image = resolve_image(app, args.image)
    region = args.region or env("FLY_REGION", DEFAULT_REGION)
    machine_env = {
        "OPENCODE_SERVER_PASSWORD": env("OPENCODE_SERVER_PASSWORD", required=True),
        "PORT": str(OPENCODE_PORT),
    }
    morph_key = env("MORPH_API_KEY")
    if not morph_key:
        print("  warning: $MORPH_API_KEY not set — machines boot but prompts will fail "
              "with provider errors", file=sys.stderr)
    else:
        machine_env["MORPH_API_KEY"] = morph_key
    if env("BROWSER_USE_API_KEY"):
        machine_env["BROWSER_USE_API_KEY"] = env("BROWSER_USE_API_KEY")
    # lane machines run the idle-loop plugin; worker one-shots never loop
    machine_env["HUNT_LOOP"] = "1" if args.role == "lane" else "0"

    state = load_state()
    print(f"creating {args.count} machine(s) in {app}/{region} from {image}")
    created = []

    def create(i):
        body = {
            "name": f"{args.prefix}-{rand_id()}",
            "region": region,
            "config": {
                "image": image,
                "guest": {
                    "cpu_kind": "shared",
                    "cpus": args.cpus,
                    "memory_mb": args.memory,
                },
                "env": machine_env,
                # Public TLS service so the lead reaches each machine over
                # https://<app>.fly.dev + fly-replay (no wireguard needed).
                # Auth is opencode basic auth; unauthenticated gets 401.
                "services": [{
                    "internal_port": OPENCODE_PORT,
                    "protocol": "tcp",
                    "autostart": True,
                    # Machines idle-driven over 6PN/wireguard generate no edge
                    # traffic; without this Fly auto-stops them mid-hunt.
                    "autostop": False,
                    "ports": [{"port": 443, "handlers": ["tls", "http"]}],
                }],
                "auto_destroy": False,
            },
        }
        _, m = mapi("POST", f"/apps/{app}/machines", body=body, timeout=180)
        return {"id": m["id"], "name": m["name"], "role": args.role,
                "ip": m.get("private_ip"),
                "instance": m.get("instance_id"),
                "created": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                         time.gmtime())}

    results = parallel(create, range(args.count))
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            print(f"  [{i}] create FAILED: {r}", file=sys.stderr)
        else:
            created.append(r)
            print(f"  created {r['name']} ({r['id']})")

    # wait for started + private ip, then health
    ready = []

    def wait_started(mc):
        deadline = time.time() + 300
        while time.time() < deadline:
            try:
                _, m = mapi("GET", f"/apps/{app}/machines/{mc['id']}", timeout=60)
            except FleetError as e:
                # Machines API flake: a created machine can 404 on GET for
                # minutes while existing in the list view — keep polling.
                if "404" in str(e):
                    time.sleep(3)
                    continue
                raise
            if m.get("state") == "started" and m.get("private_ip"):
                mc["ip"] = m["private_ip"]
                return mc
            time.sleep(3)
        raise FleetError(f"machine {mc['name']} did not start within 300s")

    healthy = []
    if created:
        for mc in parallel(wait_started, created):
            if isinstance(mc, Exception):
                print(f"  wait FAILED: {mc}", file=sys.stderr)
                continue
            healthy.append(mc)
            print(f"  started {mc['name']} at {mc['ip']}")

        if args.skip_health:
            state["machines"].extend(healthy)
            save_state(state)
            print(f"up: {len(healthy)} machine(s) started, health checks skipped "
                  "(--skip-health); wire proxies, then run: fleet.py status")
            return

        def wait_health(mc):
            deadline = time.time() + 300
            while time.time() < deadline:
                try:
                    status, _ = box_req(mc, "GET", "/global/health", timeout=15)
                    if status == 200:
                        return mc
                except FleetError:
                    pass
                time.sleep(4)
            raise FleetError(f"{mc['name']} never answered /global/health")

        ready = []
        for mc in parallel(wait_health, healthy):
            if isinstance(mc, Exception):
                print(f"  health FAILED: {mc}", file=sys.stderr)
            else:
                ready.append(mc)
                print(f"  healthy {mc['name']}")

    state["machines"].extend(ready)
    save_state(state)
    print(f"up: {len(ready)} machine(s) ready")


def cmd_list(args):
    state = load_state()
    machines = state.get("machines", [])
    if not machines:
        print("no machines in fleet-state.json")
        return
    for m in machines:
        addr = m.get("url") or m.get("ip") or "-"
        print(f"{m['id']:14}  {m.get('name','-'):24}  {m.get('backend','fly'):5}  "
              f"{m.get('role','worker'):6}  {addr}")


MAILBOX_PATH = "mailbox.jsonl"


def _read_mailbox(machine):
    """Return the raw mailbox.jsonl content of a box ('' if absent/unreachable)."""
    try:
        _, doc = box_req(machine, "GET", "/file/content?path=" + urllib.parse.quote(MAILBOX_PATH),
                         timeout=30)
        return (doc or {}).get("content", "") or ""
    except FleetError:
        return None


def _health(machine):
    try:
        status, _ = box_req(machine, "GET", "/global/health", timeout=8)
        return status == 200
    except FleetError:
        return False


def _lane_busy(machine, session_id):
    try:
        _, st = box_req(machine, "GET", "/session/status", timeout=15)
        entry = (st or {}).get(session_id, {})
        return (entry or {}).get("type") == "busy"
    except FleetError:
        return None  # unknown (unreachable)


def cmd_wait(args):
    """Block until any lane posts to its mailbox (~/mailbox.jsonl on the lane
    box) — then print the message(s) and return IMMEDIATELY. This is the
    lead's only sanctioned way to wait on lanes: never `sleep`.

    Guards: returns instantly when (a) there are no lanes (local mode: your
    subagents notify natively), (b) every lane box is unreachable (dead
    fleet), or (c) all lanes are idle AND every mailbox is fully read
    (nothing pending). On timeout it returns a status summary."""
    state = load_state()
    lanes = state.get("lanes", {})
    if not lanes:
        print("NOTHING-TO-WAIT-ON: no lanes in fleet-state.json. In local mode your subagent "
              "results arrive as notifications — do not sleep, just continue working.")
        return

    names = sorted(lanes)
    boxes = {}
    for n in names:
        mc = next((m for m in state["machines"] if m["name"] == lanes[n]["machine"]), None)
        if mc is None:
            print(f"DEAD: lane {n} points at unknown box {lanes[n]['machine']} — recreate it "
                  f"with: python3 fleet.py lane {n}")
            return
        boxes[n] = mc

    # --- guard 1: dead fleet -------------------------------------------------
    health = dict(zip(names, parallel(_health, [boxes[n] for n in names])))
    if not any(health.values()):
        dead = ", ".join(f"{n}({lanes[n]['machine']})" for n in names)
        print(f"DEAD: every lane box is unreachable ({dead}). The fleet is gone — recreate "
              "lanes (fleet.py lane <name>) or conclude the hunt; do not wait.")
        return

    # --- guard 2: nothing pending -------------------------------------------
    mail = {}
    for n, content in zip(names, parallel(_read_mailbox, [boxes[n] for n in names])):
        mail[n] = content or ""

    busy = dict(zip(names, parallel(lambda n: _lane_busy(boxes[n], lanes[n]["session"]), names)))
    pending = {n: mail[n][lanes[n].get("mail_read", 0):] for n in names}
    any_pending = any(pending[n].strip() and health.get(n) for n in names)
    any_busy = any(busy.get(n) for n in names)

    def deliver_unread():
        """Print any unread mailbox lines; return count delivered."""
        delivered = 0
        for n in names:
            start = lanes[n].get("mail_read", 0)
            content = mail[n] or ""
            if not health.get(n, True):
                continue
            fresh = content[start:]
            lines = [l for l in fresh.splitlines() if l.strip()]
            if lines:
                delivered += len(lines)
                for l in lines:
                    try:
                        obj = json.loads(l)
                        text = obj.get("text") or obj.get("msg") or l
                        kind = obj.get("kind", "msg")
                        print(f"WAKE [{n}/{kind}] {text}")
                    except json.JSONDecodeError:
                        print(f"WAKE [{n}] {l}")
            lanes[n]["mail_read"] = len(content)
        save_state(state)
        return delivered

    if deliver_unread():
        return

    if not any_busy and not any_pending:
        live = [n for n in names if health.get(n)]
        print(f"NOTHING-PENDING: all {len(live)} reachable lane(s) are idle and outboxes are "
              "read. Either assign work (fleet.py steer <lane> \"...\") or conclude — do not "
              "wait on silence.")
        return

    # --- block until a message or timeout ------------------------------------
    deadline = time.time() + args.timeout
    print(f"waiting on lanes {', '.join(n for n in names if health.get(n))} "
          f"(timeout {args.timeout}s)...")
    last_heartbeat = time.time()
    while time.time() < deadline:
        time.sleep(8)
        mail = {}
        for n, content in zip(names, parallel(_read_mailbox, [boxes[n] for n in names])):
            mail[n] = content or ""
        if deliver_unread():
            return
        if time.time() - last_heartbeat > 60:
            last_heartbeat = time.time()
            health = dict(zip(names, parallel(_health, [boxes[n] for n in names])))
            if not any(health.values()):
                print(f"DEAD: every lane box died while waiting "
                      f"({', '.join(lanes[n]['machine'] for n in names)}).")
                return
            busy = dict(zip(names, parallel(lambda n: _lane_busy(boxes[n], lanes[n]["session"]),
                                            names)))
            print(f"  ...waiting ({', '.join(f'{n}:busy' for n in names if busy.get(n)) or 'no busy lanes'})")

    # --- timeout: summarize, don't loop ---------------------------------------
    health = dict(zip(names, parallel(_health, [boxes[n] for n in names])))
    busy = dict(zip(names, parallel(lambda n: _lane_busy(boxes[n], lanes[n]["session"]), names)))
    parts = []
    for n in names:
        if not health.get(n):
            parts.append(f"{n}=unreachable")
        elif busy.get(n):
            parts.append(f"{n}=busy")
        else:
            parts.append(f"{n}=idle")
    print(f"TIMEOUT after {args.timeout}s with no lane messages. Lane state: "
          + ", ".join(parts)
          + ". Decide: steer a lane, harvest findings, or keep waiting (fleet.py wait again).")


def cmd_status(_args):
    state = load_state()
    machines = state.get("machines", [])
    if not machines:
        print("no machines in fleet-state.json")

    def probe(mc):
        out = {"name": mc["name"], "health": "down", "sessions": {}, "findings": 0}
        try:
            box_req(mc, "GET", "/global/health", timeout=15)
            out["health"] = "ok"
            _, sess = box_req(mc, "GET", "/session/status", timeout=30)
            counts = {}
            for v in (sess or {}).values():
                t = v.get("type", "?") if isinstance(v, dict) else str(v)
                counts[t] = counts.get(t, 0) + 1
            out["sessions"] = counts
            _, idx = box_req(mc, "GET", "/file/content?path=findings.d/index.jsonl", timeout=30)
            out["findings"] = len([l for l in (idx or {}).get("content", "").splitlines() if l.strip()])
        except FleetError as e:
            out["error"] = str(e)[:120]
        return out

    for out in parallel(probe, machines):
        if isinstance(out, Exception):
            print(f"  probe failed: {out}")
            continue
        sess = " ".join(f"{k}:{v}" for k, v in sorted(out["sessions"].items())) or "-"
        err = f"  ERROR {out['error']}" if out.get("error") else ""
        print(f"{out['name']:24}  health:{out['health']:4}  findings:{out['findings']:4}  "
              f"sessions[{sess}]{err}")


# --- Sessions and dispatch --------------------------------------------------

WORKER_PROMPT = """You are worker {wid} on an authorized whitehat security hunt. Obey the brief exactly.

<brief>
{brief}
</brief>

YOUR TASK FOR THIS SESSION (surgical; finish it; do not wander):
{task}

Rules: prove every claim with a copy-pasteable request + observed status/body; ALWAYS run a
control; stay under ~3 requests/second; a 401/403/429 is a result, not a failure; write/mutate
only inside resources the brief marks as yours and soft-delete anything you create. Before
testing, grep findings.d/index.jsonl (if present) so you don't repeat a known dead end.

YOUR OUTPUT IS FILES, NEVER just the chat reply. When done:
1. CREATE findings.d/{wid}-{tid}.md in exactly this format:
## <title>
status: confirmed | hypothesis | dead    class: authz|ssrf|info-leak|session|injection|config|structural|dos|crypto
sev: crit|high|med|low|info
where: <host+path>
what: <2-4 sentences>
proof: <the single request> -> <observed status and trimmed body>
control: <the falsification test> -> <result>
chain: <what it unlocks, or empty>
2. APPEND ONE JSON line per finding to findings.d/index.jsonl with exactly these keys:
{{"id","stream","worker","status","sev","class","title","where","proof","control","chain","detail"}}
One object per line, no prose. If it is not in index.jsonl and the md file, it never happened."""


def create_session(machine, title):
    _, session = box_req(machine, "POST", "/session", body={"title": title}, timeout=60)
    return session["id"]


def send_prompt(machine, session_id, text, model):
    provider, model_id = model.split("/", 1)
    body = {
        "model": {"providerID": provider, "modelID": model_id},
        "parts": [{"type": "text", "text": text}],
    }
    box_req(machine, "POST", f"/session/{session_id}/prompt_async", body=body, timeout=60)


def wait_idle(machine, session_id, timeout=900):
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, status = box_req(machine, "GET", "/session/status", timeout=30)
        st = (status or {}).get(session_id, {})
        if (st or {}).get("type") != "busy":
            return True
        time.sleep(5)
    return False


def cmd_dispatch(args):
    state = load_state()
    machines = state.get("machines", [])
    if not machines:
        die("no machines in fleet-state.json — run: python3 fleet.py up N")
    tasks = [l.rstrip("\n") for l in open(args.file) if l.strip() and not l.startswith("#")]
    if not tasks:
        die(f"no tasks in {args.file}")
    brief = open(args.brief).read() if args.brief else "(no brief supplied)"
    model = args.model or env("WORKER_MODEL", DEFAULT_WORKER_MODEL)
    stream = args.stream or os.path.splitext(os.path.basename(args.file))[0]

    print(f"dispatching {len(tasks)} task(s) across {len(machines)} machine(s), model={model}")

    def run_one(i_task):
        i, task = i_task
        mc = machines[i % len(machines)]
        tid = rand_id(6)
        sid = create_session(mc, f"worker-{stream}-{tid}")
        prompt = WORKER_PROMPT.format(wid=mc["name"], tid=tid, brief=brief, task=task)
        send_prompt(mc, sid, prompt, model)
        return {"task": task, "machine": mc["name"], "session": sid, "stream": stream}

    dispatched = []
    failures = 0
    for idx, r in enumerate(parallel(run_one, list(enumerate(tasks)))):
        if isinstance(r, Exception):
            failures += 1
            print(f"  task {idx} FAILED: {r}", file=sys.stderr)
        else:
            dispatched.append(r)
    state["dispatched"].extend(dispatched)
    save_state(state)
    print(f"dispatched {len(dispatched)} task(s), {failures} failure(s)")


def cmd_warm(args):
    """Cold-start guard: one trivial session per machine, waited to completion.

    Fresh opencode first-runs race the sqlite migration when many start at
    once and the whole batch dies. Warm serially before mass-dispatch.
    """
    state = load_state()
    machines = state.get("machines", [])
    if not machines:
        die("no machines — run: python3 fleet.py up N")
    model = args.model or env("WORKER_MODEL", DEFAULT_WORKER_MODEL)
    for mc in machines:
        try:
            sid = create_session(mc, "warm")
            send_prompt(mc, sid, "Reply with exactly: ok", model)
            ok = wait_idle(mc, sid, timeout=600)
            print(f"{mc['name']}: {'warm' if ok else 'TIMEOUT'}")
        except FleetError as e:
            print(f"{mc['name']}: FAILED {e}", file=sys.stderr)


def cmd_lane(args):
    """Start a long-lived lane-head session (judgment model) on one machine."""
    state = load_state()
    machines = state.get("machines", [])
    if args.name in state["lanes"]:
        die(f"lane {args.name!r} already exists at machine {state['lanes'][args.name]['machine']}")
    if args.machine:
        pool = [m for m in machines if m["name"] == args.machine] or \
               die(f"unknown machine {args.machine}")
    else:
        free = [m for m in machines if m.get("role") != "lane"] or machines
        pool = free
    if not pool:
        die("no machines — run: python3 fleet.py up N")
    mc = pool[0]
    mc["role"] = "lane"
    model = args.model or env("LANE_MODEL", DEFAULT_LANE_MODEL)
    brief = open(args.brief).read() if args.brief else ""
    sid = create_session(mc, f"lane-{args.name}")
    prompt = (
        f"You are the lane-head for hunt lane {args.name!r}. Read AGENTS.md and "
        "skills/whitehat-hunt/SKILL.md (both in your project root) before acting. "
        "You are REACTIVE: you never loop. Do your turn, then go idle and wait "
        "for the lead's next message.\n"
        "Your job is judgment, not volume: track proven facts, decide the next "
        "probes, and propose tasks. Write proposals as JSON lines to "
        f"lanes/{args.name}-proposals.jsonl, one object per line: "
        '{"task": "<one hypothesis, worker-sizable>", "why": "<1 sentence>", '
        '"priority": 1-5}. The lead harvests that file and dispatches workers; '
        "you never write findings.d/ and never touch the merged ledger. "
        "Grep findings.d/index.jsonl and dead-ends.md before proposing anything "
        "that may already be closed.\n"
        "MAILBOX CONTRACT — the lead blocks on this: when the lead must see "
        "something (a confirmed finding, a blocker, a question that stops you, "
        "or a completed batch of proposals), APPEND exactly one JSON line to "
        "~/mailbox.jsonl with the shell (e.g. "
        'printf \'%s\\n\' \'{"kind":"blocker","text":"need write-scope key for X"}\' >> ~/mailbox.jsonl'
        "). Kinds: finding|blocker|question|proposals|done. Be decisive: a "
        "message should make the lead's next action obvious; don't dribble "
        "observations, batch them.\n\n"
        f"<brief>\n{brief}\n</brief>\n\nBegin: survey the surface for this lane, "
        "write your first proposals to the lanes file AND post one 'proposals' "
        "mailbox line summarizing them."
    )
    send_prompt(mc, sid, prompt, model)
    state["lanes"][args.name] = {"machine": mc["name"], "session": sid, "model": model}
    save_state(state)
    print(f"lane {args.name}: session {sid} on {mc['name']} (model {model})")


def cmd_steer(args):
    state = load_state()
    lane = state["lanes"].get(args.lane)
    if not lane:
        die(f"unknown lane {args.lane!r} — known: {', '.join(state['lanes']) or '-'}")
    mc = next((m for m in state["machines"] if m["name"] == lane["machine"]), None)
    if not mc:
        die(f"lane machine {lane['machine']} not in fleet-state.json")
    send_prompt(mc, lane["session"], args.message, lane["model"])
    print(f"steered lane {args.lane} on {lane['machine']}")


# --- Harvest ----------------------------------------------------------------


def _box_get_path(machine, path):
    """Return FileContent dict or None (missing file is fine)."""
    try:
        _, content = box_req(
            machine, "GET", "/file/content?" + urllib.parse.urlencode({"path": path}), timeout=60
        )
        return content
    except FleetError:
        return None


def cmd_harvest(args):
    state = load_state()
    machines = state.get("machines", [])
    if not machines:
        print("no machines in fleet-state.json")
        return
    out_root = args.out or "."
    os.makedirs(os.path.join(out_root, "findings.d"), exist_ok=True)
    os.makedirs(os.path.join(out_root, "remote"), exist_ok=True)

    index_path = os.path.join(out_root, "findings.d", "index.jsonl")
    seen = set()
    if os.path.exists(index_path):
        for line in open(index_path):
            try:
                seen.add(json.loads(line).get("id"))
            except json.JSONDecodeError:
                pass

    def pull(mc):
        mdir = os.path.join(out_root, "remote", mc["name"])
        os.makedirs(mdir, exist_ok=True)
        new = []
        idx = _box_get_path(mc, "findings.d/index.jsonl")
        if idx and idx.get("content"):
            with open(os.path.join(mdir, "index.jsonl"), "w") as f:
                f.write(idx["content"])
            for line in idx["content"].splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("id") not in seen:
                    seen.add(obj.get("id"))
                    new.append(line)
                detail = obj.get("detail")
                if detail and isinstance(detail, str):
                    doc = _box_get_path(mc, detail)
                    if doc and doc.get("content"):
                        fname = os.path.basename(detail)
                        with open(os.path.join(mdir, fname), "w") as f:
                            f.write(doc["content"])
        # lane proposals
        try:
            _, nodes = box_req(mc, "GET", "/file?" + urllib.parse.urlencode({"path": "lanes"}),
                               timeout=30)
            for node in nodes or []:
                name = node.get("name") or ""
                if name.endswith(".jsonl") or name.endswith(".md"):
                    doc = _box_get_path(mc, f"lanes/{name}")
                    if doc and doc.get("content"):
                        os.makedirs(os.path.join(out_root, "lanes"), exist_ok=True)
                        with open(os.path.join(out_root, "lanes", f"{mc['name']}-{name}"), "w") as f:
                            f.write(doc["content"])
        except FleetError:
            pass
        return mc["name"], new

    totals = {"confirmed": 0, "hypothesis": 0, "dead": 0, "blocked": 0}
    with open(index_path, "a") as out:
        for res in parallel(pull, machines):
            if isinstance(res, Exception):
                print(f"  harvest FAILED: {res}", file=sys.stderr)
                continue
            name, new = res
            for line in new:
                out.write(line + "\n")
            for line in new:
                st = (json.loads(line).get("status") or "").lower()
                if st in totals:
                    totals[st] += 1
            print(f"  {name}: {len(new)} new finding line(s)")
    print("harvest: merged " + " ".join(f"{k}={v}" for k, v in totals.items()) +
          f" -> {index_path}")


# --- Teardown ---------------------------------------------------------------


def cmd_down(args):
    state = load_state()
    machines = state.get("machines", [])
    if not machines:
        print("nothing to destroy")
        return
    if not args.force:
        print("harvesting before destroy (use --force to skip)")
        try:
            cmd_harvest(argparse.Namespace(out=args.out))
        except Exception as e:  # noqa: BLE001
            print(f"  harvest failed ({e}); refusing to destroy without --force", file=sys.stderr)
            sys.exit(1)

    for res in parallel(destroy_machine, machines):
        if isinstance(res, Exception):
            print(f"  destroy FAILED: {res}", file=sys.stderr)
        else:
            print(f"  destroyed {res}")
    state["machines"] = []
    state["lanes"] = {}
    save_state(state)
    print("down: fleet destroyed")


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(prog="fleet.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("up", help="create worker boxes")
    p.add_argument("count", type=int)
    p.add_argument("--backend", choices=["local", "boat", "fly"],
                   default=None, help="default: $FLEET_BACKEND or local")
    p.add_argument("--type", default=None,
                   help="boat: sandbox size small|default|large|xlarge (default: $BOAT_TYPE or small)")
    p.add_argument("--ttl", type=int, default=None,
                   help="boat: sandbox lifetime seconds (default: $BOAT_TTL or 14400)")
    p.add_argument("--from", dest="from_snapshot", metavar="SNAPSHOT",
                   help="boat: clone from a saved snapshot")
    p.add_argument("--image", help="fly: registry.fly.io/<app>:<tag> (default: reuse an existing machine's)")
    p.add_argument("--region")
    p.add_argument("--cpus", type=int, default=2)
    p.add_argument("--memory", type=int, default=2048)
    p.add_argument("--prefix", default="hunt")
    p.add_argument("--role", default="worker", choices=["worker", "lane"])
    p.add_argument("--skip-health", action="store_true",
                   help="fly: save machines to state after start without waiting for "
                        "/global/health (use with the fly-proxy fallback when the "
                        "host has no Fly wireguard)")
    p.set_defaults(fn=cmd_up)

    p = sub.add_parser("list", help="list machines in the app")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("status", help="per-machine health, sessions, finding counts")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("wait", help="block until any lane posts to its mailbox — the lead's "
                                    "only sanctioned way to wait on lanes (never sleep)")
    p.add_argument("--timeout", type=int, default=300,
                   help="max seconds to block (default 300); on timeout returns a status summary")
    p.set_defaults(fn=cmd_wait)

    p = sub.add_parser("warm", help="cold-start guard: one trivial session per machine, serially")
    p.add_argument("--model")
    p.set_defaults(fn=cmd_warm)

    p = sub.add_parser("dispatch", help="one fresh session per task line, fanned out over machines")
    p.add_argument("-f", "--file", required=True, help="tasks file, one hypothesis per line")
    p.add_argument("--brief", help="brief.md embedded into every worker prompt")
    p.add_argument("--model")
    p.add_argument("--stream", help="name for the stream (default: task file name)")
    p.set_defaults(fn=cmd_dispatch)

    p = sub.add_parser("lane", help="start a long-lived lane-head session")
    p.add_argument("name")
    p.add_argument("--machine")
    p.add_argument("--model")
    p.add_argument("--brief")
    p.set_defaults(fn=cmd_lane)

    p = sub.add_parser("steer", help="send a message into a lane-head session")
    p.add_argument("lane")
    p.add_argument("message")
    p.set_defaults(fn=cmd_steer)

    p = sub.add_parser("harvest", help="pull findings.d + lanes from every machine and merge")
    p.add_argument("--out", help="harvest root (default: current dir)")
    p.set_defaults(fn=cmd_harvest)

    p = sub.add_parser("down", help="destroy all machines (harvests first unless --force)")
    p.add_argument("--force", action="store_true")
    p.add_argument("--out", help="harvest root for the pre-destroy harvest")
    p.set_defaults(fn=cmd_down)

    args = parser.parse_args()
    try:
        args.fn(args)
    except FleetError as e:
        die(str(e))


if __name__ == "__main__":
    main()
