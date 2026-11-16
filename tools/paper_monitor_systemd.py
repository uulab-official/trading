"""Render inert systemd user service/timer for the PAPER monitoring tick.

Pure rendering only: it never installs, starts, enables or contacts systemd.
"""
import argparse
import json
from pathlib import Path
import re
import sys

_SAFE = re.compile(r"/[A-Za-z0-9_./-]+\Z")
_NAME = re.compile(r"[a-z][a-z0-9-]{0,39}\Z")


class Refused(ValueError):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Refused("invalid_arguments")


def _path(raw):
    if type(raw) is not str or len(raw) > 1024 or _SAFE.fullmatch(raw) is None:
        raise Refused("invalid_path")
    value = Path(raw)
    if ".." in value.parts or str(value) != raw or raw == "/":
        raise Refused("invalid_path")
    return raw


def _args(argv):
    p = Parser(add_help=False, allow_abbrev=False)
    p.add_argument("--python", required=True)
    p.add_argument("--tool", required=True)
    p.add_argument("--service", required=True)
    p.add_argument("--capture", required=True)
    p.add_argument("--account", required=True)
    p.add_argument("--directory", required=True)
    p.add_argument("--unit-name", default="paper-monitor")
    p.add_argument("--interval-seconds", type=int, default=60)
    p.add_argument("--timeout-seconds", type=int, default=10)
    args = p.parse_args(argv)
    for key in ("python", "tool", "service", "capture", "account", "directory"):
        setattr(args, key, _path(getattr(args, key)))
    if (_NAME.fullmatch(args.unit_name) is None
            or not 30 <= args.interval_seconds <= 3600
            or not 1 <= args.timeout_seconds <= 30
            or args.interval_seconds < args.timeout_seconds * 3 + 10):
        raise Refused("invalid_limits")
    if not args.tool.endswith("/tools/paper_monitor_tick.py"):
        raise Refused("unexpected_tool")
    return args


def render(args):
    """Return reviewable, inactive unit content, with no host side effects."""
    name = args.unit_name
    argv = [
        args.python, "-I", "-S", "-B", args.tool,
        "--service", args.service, "--capture", args.capture,
        "--account", args.account, "--directory", args.directory,
        "--timeout-seconds", str(args.timeout_seconds),
    ]
    # Reject spaces, %, backslash, newline, shell metacharacters instead
    # of relying on systemd or shell quoting. ExecStart never invokes a shell.
    if any(_SAFE.fullmatch(arg) is None for arg in argv
           if arg.startswith("/")):
        raise Refused("invalid_argv")
    service = (
        "[Unit]\n"
        "Description=Read-only PAPER service/capture/account observation\n"
        "Documentation=man:systemd.service(5)\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "ExecStart=" + " ".join(argv) + "\n"
        "NoNewPrivileges=yes\n"
        "PrivateTmp=yes\n"
        "PrivateDevices=yes\n"
        "ProtectSystem=strict\n"
        "ReadWritePaths=" + args.directory + "\n"
        "RestrictAddressFamilies=AF_UNIX\n"
        "TasksMax=32\n"
        "MemoryMax=256M\n"
        "TimeoutStartSec=" + str(args.timeout_seconds * 3 + 10) + "s\n"
        "UMask=0077\n"
    )
    timer = (
        "[Unit]\n"
        "Description=Periodic local PAPER observation (operator activation only)\n\n"
        "[Timer]\n"
        "OnBootSec=" + str(args.interval_seconds) + "s\n"
        "OnUnitActiveSec=" + str(args.interval_seconds) + "s\n"
        "AccuracySec=5s\n"
        "RandomizedDelaySec=0\n"
        "Unit=" + name + ".service\n"
        "Persistent=false\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    return {
        "schema_version": 1, "source": "paper_monitor_systemd",
        "status": "rendered_only", "exit_code": 0,
        "files": {name + ".service": service, name + ".timer": timer},
        "boundaries": {
            "installed": False, "enabled": False, "activated": False,
            "external_notification_delivered": False,
            "broker_orders": False, "production_ready": False,
        },
    }


def main(argv=None):
    try:
        result = render(_args(argv))
        code = 0
    except (Refused, ValueError, TypeError):
        code = 2
        result = {
            "schema_version": 1, "source": "paper_monitor_systemd",
            "status": "refused", "exit_code": 2, "files": {},
            "boundaries": {
                "installed": False, "enabled": False, "activated": False,
                "external_notification_delivered": False,
                "broker_orders": False, "production_ready": False,
            },
        }
    try:
        print(json.dumps(result, sort_keys=True, ensure_ascii=True), flush=True)
    except OSError:
        return 4
    return code


if __name__ == "__main__":
    sys.exit(main())
