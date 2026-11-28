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
  WORKER_MODEL               muscle model id (default: morph-abliterated/morph-dsv41flash-abliterated)
  LANE_MODEL                 lane-head/coordinator model id (default: morph/morph-kimik3)
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
import http.client
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
DEFAULT_LANE_MODEL = "morph/morph-kimik3"
OPENCODE_PORT = 4096
HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))


class FleetError(RuntimeError):
    """User-actionable error."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


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
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE_FILE)
    directory = os.open(".", os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def rand_id(n=6):
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(n))


# --- HTTP helpers -----------------------------------------------------------


def _request(url, method="GET", body=None, headers=None, timeout=120, direct=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    # direct=True ignores HTTP(S)_PROXY. urllib.request has no module-level open(), so the
    # proxied path must use urlopen.
    open_url = (urllib.request.build_opener(urllib.request.ProxyHandler({})).open if direct
                else urllib.request.urlopen)
    try:
        with open_url(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise FleetError(f"{method} {url} -> {e.code}: {detail}", status=e.code)
    except urllib.error.URLError as e:
        raise FleetError(
            f"{method} {url} failed: {e.reason}. If this is a machine address, "
            "check your Fly wireguard: fly wireguard status / fly wireguard up"
        )
    except (OSError, http.client.HTTPException) as e:
        # A read timeout or a dropped connection is not wrapped in URLError. Report it as a
        # FleetError so retry loops (health checks, dispatch) treat it like any other failure.
        raise FleetError(f"{method} {url} failed: {e!r}")


def mapi(method, path, body=None, timeout=120):
    token = env("FLY_API_TOKEN", required=True)
    return _request(
        MACHINES_API + path,
        method=method,
        body=body,
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
        direct=True,
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
    # Operator hunts may use the public anycast replay path. Paid workers are
    # deliberately privateOnly and must use their private Fly6PN address.
    if machine.get("instance") and not machine.get("privateOnly"):
        app = env("FLY_APP", required=True)
        url = f"https://{app}.fly.dev{path}"
        headers["fly-replay"] = f"instance={machine['instance']}"
    else:
        ip = machine.get("ip")
        if not ip:
            raise FleetError(f"machine {machine.get('name') or machine.get('id')} has no address")
        port = machine.get("port") or int(env("OPENCODE_PORT", OPENCODE_PORT))
        authority = f"[{ip}]" if ":" in ip else ip
        url = f"http://{authority}:{port}{path}"
    return _request(
        url, method=method, body=body, headers=headers, timeout=timeout,
        direct=bool(machine.get("privateOnly")),
    )


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
    try:
        mapi("DELETE", f"/apps/{app}/machines/{mc['id']}?force=true", timeout=60)
    except FleetError as exc:
        if exc.status != 404:
            raise
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


def image_source_app(default_app):
    """Return the controller's image-source app, distinct from paid workers."""
    return env("HACK_ME_CONTROL_FLY_APP", default_app)


def resolve_image(app, args_image):
    if args_image:
        return args_image
    # A controller holding a token scoped to the worker app cannot list the controller app's
    # machines; it names the worker image explicitly instead.
    if env("HACK_ME_WORKER_IMAGE"):
        return env("HACK_ME_WORKER_IMAGE")
    image = find_machine_image(app)
    if image:
        return image
    raise FleetError(
        "no --image given and the app has no machines to copy one from. "
        "Deploy the image first (fly deploy --build-only) or pass --image "
        "registry.fly.io/<app>:<tag>"
    )


def scoped_proxy_environment(machine_env):
    """Copy the paid engagement's scoped proxy contract to worker Machines.

    Blank NO_PROXY is intentional. Only the unauthenticated proxy URL and its
    matching lowercase variables are copied; never copy unrelated env values.
    """
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                 "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        if name in os.environ:
            machine_env[name] = os.environ[name]
    return machine_env


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


def worker_inference_environment():
    """Return the worker's model transport, never the upstream provider key.

    Paid engagement workers receive only the control-plane broker URL and the
    per-engagement token the broker already expects. Operator hunts retain the
    existing MORPH_API_KEY behavior.
    """
    broker_url = env("HACK_ME_MODEL_BROKER_URL")
    if broker_url:
        return {
            "HACK_ME_MODEL_BROKER_URL": broker_url,
            "HACK_ME_BROKER_TOKEN": env("HACK_ME_BROKER_TOKEN", required=True),
        }
    return {"MORPH_API_KEY": env("MORPH_API_KEY")} if env("MORPH_API_KEY") else {}


PAID_WORKER_CONFIG_FIELDS = (
    "HACK_ME_PAID_FIREWALL",
    "HACK_ME_TARGET_HOSTNAME",
    "HACK_ME_CONTROL_PRIVATE_IP",
    "HACK_ME_EGRESS_ENFORCED",
    "HACK_ME_EGRESS_PROXY_URL",
    "HACK_ME_EGRESS_RATE",
    "HACK_ME_WORKER_SSH_FROM",
)


def paid_worker_environment(machine_env):
    """Copy only the deployment's explicitly required paid-worker controls."""
    for name in PAID_WORKER_CONFIG_FIELDS:
        if name in os.environ:
            machine_env[name] = os.environ[name]
    return machine_env


def fly_worker_services():
    """Paid workers have no anycast service; only private 6PN can reach them."""
    if env("HACK_ME_PAID_ENGAGEMENT") == "1":
        return []
    return [{
        "internal_port": OPENCODE_PORT,
        "protocol": "tcp",
        "autostart": True,
        # Operator machines idle-driven over 6PN must not auto-stop mid-hunt.
        "autostop": False,
        "ports": [{"port": 443, "handlers": ["tls", "http"]}],
    }]


# Machines API answers to a create that guarantee nothing was allocated: a
# malformed or unprocessable body, bad or insufficient auth, an unknown app.
# Everything else (no status: connection error or timeout; 408, 409, 429, 5xx)
# may have allocated a machine, so its cleanup intent is kept until Fly's
# listing settles it.
CREATE_REJECTED_STATUSES = frozenset({400, 401, 403, 404, 422})
# An id-less intent (the create got no definitive answer) is dropped as "never allocated" only
# after it has been missing from at least INTENT_DROP_MISSES consecutive listings AND its create
# started longer ago than the settle window: the 180 s create timeout plus Fly listing lag.
# Until then cleanup stays pending, and a machine Fly did create late is still found by its
# name and engagement metadata and destroyed. Misses and timestamps live in the journal, so a
# restarted controller continues the count instead of starting over.
INTENT_DROP_MISSES = 3
INTENT_SETTLE_FLOOR_SECONDS = 240


def intent_settle_seconds():
    return max(INTENT_SETTLE_FLOOR_SECONDS, int(env("HACK_ME_INTENT_SETTLE_SECONDS", "600")))


def require_isolated_worker_app(app):
    """Paid workers must run in an app of their own that holds no Fly secrets.

    Fly injects every app secret into every Machine of the app, including Machines created
    through the Machines API with an explicit env. A worker in the controller's app, or in an
    app someone ran `fly secrets set` on, would hand the agent those values (the controller's
    app holds FLY_API_TOKEN and MORPH_API_KEY). Fails closed: if the secret list cannot be read,
    nothing is created.
    """
    controller_apps = {name for name in (env("HACK_ME_CONTROL_FLY_APP"), env("FLY_APP_NAME")) if name}
    if app in controller_apps:
        raise FleetError(f"refusing to launch paid workers in the controller's own app {app!r}")
    try:
        _, listing = mapi("GET", f"/apps/{app}/secrets", timeout=30)
        secrets = listing["secrets"]
        if not isinstance(secrets, list):
            raise TypeError("secrets is not a list")
    except Exception as exc:  # noqa: BLE001 - any failure to prove "no secrets" refuses the launch
        raise FleetError(f"refusing to launch paid workers: cannot list {app!r} secrets ({type(exc).__name__})")
    if secrets:
        names = ", ".join(sorted(str(item.get("name", "?")) for item in secrets if isinstance(item, dict)))
        raise FleetError(
            f"refusing to launch paid workers: app {app!r} has Fly secrets ({names or len(secrets)}), "
            "which Fly injects into every worker; unset them or use a dedicated worker app")


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
    paid = env("HACK_ME_PAID_ENGAGEMENT") == "1"
    if paid:
        # Before any image lookup or create: nothing is allocated for a refused launch.
        require_isolated_worker_app(app)
    image = resolve_image(image_source_app(app), args.image)
    region = args.region or env("FLY_REGION", DEFAULT_REGION)
    machine_env = {
        "OPENCODE_SERVER_PASSWORD": env("OPENCODE_SERVER_PASSWORD", required=True),
        "PORT": str(OPENCODE_PORT),
    }
    machine_env.update(worker_inference_environment())
    if env("BROWSER_USE_API_KEY") and not paid:
        # Operator hunts only: a paid worker never receives a third-party credential.
        machine_env["BROWSER_USE_API_KEY"] = env("BROWSER_USE_API_KEY")
    scoped_proxy_environment(machine_env)
    paid_worker_environment(machine_env)
    # Only the coordinator loops. Lane-heads are reactive and workers are one-shot.
    machine_env["HUNT_LOOP"] = "1" if args.role == "coordinator" else "0"
    if paid and args.role in ("coordinator", "lane"):
        if env("HACK_ME_LOCAL_SUBAGENTS") == "1":
            machine_env["HACK_ME_LOCAL_SUBAGENTS"] = "1"
        else:
            machine_env["HACK_ME_CONTROL_URL"] = env("HACK_ME_CONTROL_URL", required=True)
        for filename, variable in (("brief.md", "HACK_ME_PAID_BRIEF_B64"),
                                   ("scope.md", "HACK_ME_PAID_SCOPE_B64")):
            with open(filename, "rb") as source:
                machine_env[variable] = base64.b64encode(source.read()).decode()

    state = load_state()
    print(f"creating {args.count} machine(s) in {app}/{region} from {image}")
    created = []
    engagement_id = getattr(args, "engagement_id", None)
    journal_lock = threading.Lock()

    def create(i):
        name = f"{args.prefix}-{rand_id()}"
        body = {
            "name": name,
            "region": region,
            "config": {
                "image": image,
                "guest": {
                    "cpu_kind": "shared",
                    "cpus": args.cpus,
                    "memory_mb": args.memory,
                },
                "env": machine_env,
                "services": fly_worker_services(),
                "auto_destroy": False,
            },
        }
        if engagement_id:
            # Record intent before the provider can allocate a machine. Track
            # cleanup separately from the set of healthy, dispatchable workers.
            body["config"]["metadata"] = {"engagement_id": engagement_id}
            with journal_lock:
                state.setdefault("cleanupMachines", {})[name] = {
                    "app": app, "engagement": engagement_id, "name": name,
                    "createdAt": time.time(),
                }
                save_state(state)
        try:
            _, m = mapi("POST", f"/apps/{app}/machines", body=body, timeout=180)
        except FleetError as exc:
            if engagement_id and exc.status in CREATE_REJECTED_STATUSES:
                # Fly refused the request outright, so no machine exists to find
                # later. Leaving the id-less intent would keep cleanup (and the
                # engagement's capacity slot) unconfirmed forever.
                with journal_lock:
                    state["cleanupMachines"].pop(name, None)
                    save_state(state)
            raise
        if engagement_id:
            with journal_lock:
                state["cleanupMachines"][name]["id"] = m["id"]
                save_state(state)
        return {"id": m["id"], "name": m["name"], "role": args.role,
                "ip": m.get("private_ip"),
                "instance": m.get("instance_id"),
                "privateOnly": env("HACK_ME_PAID_ENGAGEMENT") == "1",
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

        # An engagement never uses a machine that failed to start or answer health, so do not
        # leave it running (and billing) until teardown. Its journal entry stays: cmd_down
        # retries the delete and treats a 404 as done.
        ready_ids = {mc["id"] for mc in ready}
        unusable = [mc for mc in created if mc["id"] not in ready_ids]
        if engagement_id and unusable:
            for mc, res in zip(unusable, parallel(destroy_machine, unusable)):
                outcome = f"FAILED ({res}); teardown retries it" if isinstance(res, BaseException) else "destroyed"
                print(f"  unhealthy {mc['name']}: {outcome}", file=sys.stderr)

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


def reject_pending_permissions(machine, session_id=None):
    """Reject every opencode permission prompt waiting on this machine (optionally one session).

    Nobody answers prompts on a worker, so a prompt parks the session until the run deadline.
    fly/opencode.json decides the prompts opencode is known to raise. This catches any new one a
    future opencode adds, and it rejects rather than approves, so it never grants anything.
    Returns the number rejected; listing errors are left to the caller's status check."""
    try:
        _, pending = box_req(machine, "GET", "/permission", timeout=15)
    except FleetError:
        return 0
    rejected = 0
    for request in pending if isinstance(pending, list) else []:
        if not isinstance(request, dict) or not request.get("id"):
            continue
        if session_id and request.get("sessionID") != session_id:
            continue
        try:
            box_req(machine, "POST", f"/permission/{request['id']}/reply",
                    body={"reply": "reject", "message": "No operator is present. Use the project "
                          "directory or continue without this action."}, timeout=15)
            rejected += 1
            print(f"  {machine.get('name')}: rejected unattended {request.get('permission')} prompt",
                  file=sys.stderr)
        except FleetError:
            pass
    return rejected


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
status: confirmed | hypothesis | dead    class: authz|ssrf|info-leak|session|injection|config|structural|dos|crypto|cve
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
    machines = [m for m in state.get("machines", []) if m.get("role", "worker") == "worker"]
    if not machines:
        die("no worker machines in fleet-state.json — run: python3 fleet.py up N")
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
        return {"task": task, "taskId": tid, "machine": mc["name"], "session": sid, "stream": stream}

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
    failures = 0
    for mc in machines:
        try:
            session_model = DEFAULT_LANE_MODEL if mc.get("role") in ("coordinator", "lane") else model
            sid = create_session(mc, "warm")
            send_prompt(mc, sid, "Reply with exactly: ok", session_model)
            ok = wait_idle(mc, sid, timeout=600)
            if not ok:
                failures += 1
            print(f"{mc['name']}: {'warm' if ok else 'TIMEOUT'}")
        except FleetError as e:
            failures += 1
            print(f"{mc['name']}: FAILED {e}", file=sys.stderr)
    return failures


def cmd_lane(args):
    """Start a long-lived lane-head session (judgment model) on one machine."""
    state = load_state()
    machines = state.get("machines", [])
    if args.name in state["lanes"]:
        die(f"lane {args.name!r} already exists at machine {state['lanes'][args.name]['machine']}")
    free = [m for m in machines if m.get("role", "worker") == "worker"]
    if args.machine:
        pool = [m for m in free if m["name"] == args.machine] or die(f"unknown free worker {args.machine}")
    else:
        pool = free
    if len(free) < 2:
        die("keep at least one worker available for dispatch after creating this lane")
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
        if idx is None and env("HACK_ME_PAID_ENGAGEMENT") == "1":
            raise FleetError(f"paid harvest could not read findings.d/index.jsonl from {mc['name']}")
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
        if env("HACK_ME_PAID_ENGAGEMENT") == "1":
            # detail is prose, not a file path. Enumerate the actual serialization documents.
            _, nodes = box_req(mc, "GET", "/file?" + urllib.parse.urlencode({"path": "findings.d"}), timeout=30)
            for node in nodes or []:
                name = node.get("name") or ""
                if not name.endswith(".md") or os.path.basename(name) != name:
                    continue
                doc = _box_get_path(mc, f"findings.d/{name}")
                if doc is None:
                    raise FleetError(f"paid harvest could not read findings.d/{name} from {mc['name']}")
                with open(os.path.join(mdir, name), "w") as output:
                    output.write(doc.get("content", ""))
        # Paid workers' egress record (DNS queries, new and denied connections, proxy log),
        # kept by root under /var/log/hack-me and linked into the project as egress/.
        for log_name in ("connections.log", "dns.log", "proxy.log"):
            doc = _box_get_path(mc, f"egress/{log_name}")
            if doc and doc.get("content"):
                with open(os.path.join(mdir, f"egress-{log_name}"), "w") as f:
                    f.write(doc["content"])
        if env("HACK_ME_PAID_ENGAGEMENT") == "1":
            # Preserve OpenCode's coordinator/lane/worker sessions before their ephemeral machines
            # are destroyed. These are operator artifacts, never part of Landing's public report.
            try:
                _, sessions = box_req(mc, "GET", "/session", timeout=30)
                session_dir = os.path.join(out_root, "sessions")
                os.makedirs(session_dir, exist_ok=True)
                for session in sessions or []:
                    session_id = session.get("id")
                    if not session_id:
                        continue
                    _, messages = box_req(mc, "GET", f"/session/{session_id}/message", timeout=60)
                    with open(os.path.join(session_dir, f"{mc['name']}-{session_id}.json"), "w") as output:
                        json.dump({"machine": mc["name"], "session": session, "messages": messages}, output)
            except (FleetError, TypeError) as exc:
                raise FleetError(f"paid session capture failed on {mc['name']}: {exc}") from exc
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
                        # A lane may already be named with its machine prefix from a prior mirrored
                        # harvest. Do not prepend that prefix again on every successive harvest.
                        harvested_name = name if name.startswith(f"{mc['name']}-") else f"{mc['name']}-{name}"
                        with open(os.path.join(out_root, "lanes", harvested_name), "w") as f:
                            f.write(doc["content"])
        except FleetError:
            pass
        return mc["name"], new

    totals = {"confirmed": 0, "hypothesis": 0, "dead": 0, "blocked": 0}
    failures = 0
    with open(index_path, "a") as out:
        for res in parallel(pull, machines):
            if isinstance(res, Exception):
                failures += 1
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
    if failures and env("HACK_ME_PAID_ENGAGEMENT") == "1":
        raise FleetError(f"paid harvest failed on {failures} machine(s)")


# --- Teardown ---------------------------------------------------------------


def cmd_down(args):
    state = load_state()
    machines = list(state.get("machines", []))
    journal = state.get("cleanupMachines", {})
    unresolved = []
    dropped = []
    known_ids = {machine["id"] for machine in machines}
    inventory = None
    for name, intent in journal.items():
        if intent["app"] != env("FLY_APP", required=True):
            raise FleetError("cleanup is waiting for the original worker application")
        machine_id = intent.get("id")
        if machine_id is None:
            if inventory is None:
                _, inventory = mapi("GET", f"/apps/{intent['app']}/machines", timeout=60)
            matches = [mc for mc in inventory if mc.get("name") == name and
                       (mc.get("config") or {}).get("metadata", {}).get("engagement_id") == intent["engagement"]]
            if len(matches) > 1:
                unresolved.append(name)
                continue
            if not matches:
                # An interrupted create may still become visible. Absence from one listing
                # cannot confirm the request never allocated; absence from several listings
                # after the create could no longer be in flight can.
                now = time.time()
                intent.setdefault("firstMissAt", now)
                intent["misses"] = int(intent.get("misses", 0)) + 1
                started = intent.get("createdAt", intent["firstMissAt"])
                if intent["misses"] >= INTENT_DROP_MISSES and now - started >= intent_settle_seconds():
                    print(f"  {name}: create never produced a machine "
                          f"({intent['misses']} listings over {int(now - started)}s); dropping its intent")
                    dropped.append(name)
                else:
                    unresolved.append(name)
                save_state(state)
                continue
            machine_id = matches[0]["id"]
            intent["id"] = machine_id
            save_state(state)
        if machine_id not in known_ids:
            machines.append({"name": name, "id": machine_id})
            known_ids.add(machine_id)
    for name in dropped:
        journal.pop(name, None)
    if dropped:
        save_state(state)
    if not machines and not unresolved:
        print("nothing to destroy")
        return
    if not args.force:
        print("harvesting before destroy (use --force to skip)")
        try:
            cmd_harvest(argparse.Namespace(out=args.out))
        except Exception as e:  # noqa: BLE001
            print(f"  harvest failed ({e}); refusing to destroy without --force", file=sys.stderr)
            sys.exit(1)

    failures = []
    for machine, res in zip(machines, parallel(destroy_machine, machines)):
        if isinstance(res, BaseException):
            failures.append(machine)
            print(f"  destroy FAILED: {res}", file=sys.stderr)
        else:
            print(f"  destroyed {res}")
    if journal:
        failed_ids = {machine["id"] for machine in failures}
        state["cleanupMachines"] = {
            name: item for name, item in journal.items()
            if name in unresolved or item.get("id") in failed_ids
        }
    if failures or unresolved:
        state["machines"] = failures
        retry_names = {machine["name"] for machine in failures}
        state["lanes"] = {
            name: lane for name, lane in state.get("lanes", {}).items()
            if lane.get("machine") in retry_names
        }
        # Keep resolved successes out of retries; a partial teardown must
        # converge on a later invocation instead of repeating successful work.
        save_state(state)
        raise FleetError(
            f"cleanup pending for {len(failures)} failed deletions and {len(unresolved)} unconfirmed allocations"
        )
    state["machines"] = []
    state["lanes"] = {}
    save_state(state)
    print("down: fleet destroyed")


def run_paid_remote(args):
    """Forward only canonical read/plan/dispatch operations to this run's controller."""
    allowed = {"list", "status", "wait", "dispatch", "lane", "steer", "harvest"}
    if args.command not in allowed:
        raise FleetError("paid fleets are pre-provisioned; this operation is controller-only")
    values = {key: value for key, value in vars(args).items() if key not in {"fn", "command"}}
    values["command"] = args.command
    if args.command == "dispatch":
        values["tasks"] = open(args.file).read()
    elif args.command == "lane":
        # Lane session prompts use snippets from the coordinator's canonical brief.
        values["briefText"] = open(args.brief).read() if args.brief else ""
    token = env("HACK_ME_BROKER_TOKEN", required=True)
    _, result = _request(
        env("HACK_ME_CONTROL_URL", required=True), method="POST", body=values,
        headers={"Authorization": f"Bearer {token}"}, timeout=180, direct=True,
    )
    result = result or {}
    for name, content in result.get("files", {}).items():
        if name in ("findings.d/index.jsonl", "findings.md", "dead-ends.md"):
            destination = name
        elif name.startswith("lanes/") and "/" not in name.removeprefix("lanes/") and ".." not in name:
            destination = name
        else:
            continue
        os.makedirs(os.path.dirname(destination) or ".", exist_ok=True)
        with open(destination, "w") as output:
            output.write(content)
    print(result.get("output", ""), end="")


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
    p.add_argument("--role", default="worker", choices=["worker", "lane", "coordinator"])
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
    if env("HACK_ME_CONTROL_URL"):
        return run_paid_remote(args)
    try:
        args.fn(args)
    except FleetError as e:
        die(str(e))


if __name__ == "__main__":
    main()
