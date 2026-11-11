#!/usr/bin/env python3
"""Run browser-use in an isolated Browser Use cloud browser, never local Chrome.

Standalone stdlib script (python3 clis/browser.py ...). Shells out to the
installed `browser-use` harness and always names a daemon, so no call can fall
back to the operator's local Chrome profile. Needs `uv tool install browser-use`
plus `browser-use auth login` (interactive) or BROWSER_USE_API_KEY (headless).

Typical use during recon that needs JavaScript, a real cookie jar, or a clean
IP that survives bot protection (use curl for everything else):

  python3 clis/browser.py up ph-<target>
  printf 'new_tab("https://example.com")\nprint(page_info())\n' \
    | python3 clis/browser.py run ph-<target>
  python3 clis/browser.py down ph-<target>

Cloud browsers bill until stopped: always down the daemon when done.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
DEFAULT_TIMEOUT_MINUTES = 30
DEFAULT_COUNTRY = "us"


class BrowserError(RuntimeError):
    """A user-actionable browser CLI error."""


def validate_name(name):
    if not NAME_RE.match(name or ""):
        raise BrowserError(
            f"invalid daemon name {name!r}: use 1-64 characters from [A-Za-z0-9_-]"
        )
    return name


def binary():
    override = os.environ.get("PENHARNESS_BROWSER_USE")
    if override:
        return override
    found = shutil.which("browser-use")
    if not found:
        raise BrowserError(
            "browser-use is not installed. Run: uv tool install browser-use "
            "(or set PENHARNESS_BROWSER_USE to its path)"
        )
    return found


def environment(name, live=False):
    env = dict(os.environ)
    env["BU_NAME"] = name
    if not live:
        env["BH_OPEN_LIVE_URL"] = "0"
    return env


def cloud_guard(name, timeout=None, country=None, profile=None):
    """Build the leading statement that keeps browser-harness off local Chrome.

    browser-harness skips its local-bootstrap path only when the submitted code
    starts with `start_remote_daemon(` or `stop_remote_daemon(`. Starting with a
    no-op-on-live call keeps every call on the cloud browser and re-provisions it
    if the daemon died. Creation kwargs apply only when a browser is created.
    """
    arguments = [repr(name)]
    if timeout is not None:
        if not 1 <= timeout <= 240:
            raise BrowserError("--timeout must be 1..240 minutes")
        arguments.append(f"timeout={timeout}")
    if country:
        arguments.append(f"proxyCountryCode={country!r}")
    if profile:
        arguments.append(f"profileName={profile!r}")
    return (
        f"start_remote_daemon({', '.join(arguments)}) "
        f"if not daemon_alive({name!r}) else None\n"
    )


def attach_code(name, script):
    return cloud_guard(name) + script


def stop_code(name):
    return f"stop_remote_daemon({name!r})\nprint('stopped')\n"


def invoke(code, name, live=False):
    result = subprocess.run(
        [binary()],
        input=code,
        text=True,
        env=environment(name, live=live),
        capture_output=True,
    )
    output = {
        "daemon": name,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }
    return output


def auth_status():
    result = subprocess.run(
        [binary(), "auth", "status"], text=True, capture_output=True
    )
    if result.returncode != 0:
        raise BrowserError(result.stderr.strip() or "browser-use auth status failed")
    try:
        return json.loads(result.stdout)
    except ValueError:
        return {"status": result.stdout.strip()}


def require_auth():
    status = auth_status()
    if status.get("status") != "authenticated":
        raise BrowserError(
            "Browser Use Cloud is not authenticated. Run: browser-use auth login "
            "(or export BROWSER_USE_API_KEY for headless hosts)"
        )
    return status


def read_script(path):
    if path in (None, "-"):
        if sys.stdin.isatty():
            raise BrowserError("no script: pass a file or pipe code on stdin")
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    up = commands.add_parser("up", help="Provision a cloud browser and its daemon")
    up.add_argument("name")
    up.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_MINUTES,
                    help=f"Cloud browser lifetime in minutes (default {DEFAULT_TIMEOUT_MINUTES})")
    up.add_argument("--country", default=DEFAULT_COUNTRY,
                    help=f"Proxy country ISO2 (default {DEFAULT_COUNTRY}; 'none' disables)")
    up.add_argument("--profile", help="Browser Use cloud profile name for signed-in cookies")
    up.add_argument("--live", action="store_true", help="Print/open the cloud live-view URL")

    run = commands.add_parser("run", help="Run browser-use helper code in the cloud browser")
    run.add_argument("name")
    run.add_argument("script", nargs="?", help="Python file, or '-' / omit for stdin")
    run.add_argument("--live", action="store_true", help="Print/open the cloud live-view URL")

    down = commands.add_parser("down", help="Stop the daemon and its billable cloud browser")
    down.add_argument("name")

    commands.add_parser("status", help="Show CLI, auth, and daemon health")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        validate_name(args.name) if args.command != "status" else None
        if args.command == "status":
            result = {"binary": binary(), "auth": auth_status()}
        elif args.command == "up":
            require_auth()
            country = None if args.country.lower() in {"none", "off", ""} else args.country
            code = cloud_guard(args.name, args.timeout, country, args.profile) + "print('ready')\n"
            result = invoke(code, args.name, live=args.live)
        elif args.command == "run":
            require_auth()
            result = invoke(attach_code(args.name, read_script(args.script)),
                            args.name, live=args.live)
        else:
            result = invoke(stop_code(args.name), args.name, live=getattr(args, "live", False))
        print(json.dumps(result, indent=2))
        return 0 if result.get("returncode", 0) == 0 else 1
    except (BrowserError, OSError, UnicodeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
