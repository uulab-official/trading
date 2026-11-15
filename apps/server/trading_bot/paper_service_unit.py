"""Generate an inactive, no-clobber systemd user unit for a fixed PAPER pair."""
import hashlib
import os
from pathlib import Path
import re

from .local_report_io import publish_new_report
from .paper_service_state import ServiceDirectory, ServiceStorageError, ServiceUnavailable


def _absolute_path(value):
    try:
        text = os.fspath(value)
    except TypeError:
        raise ServiceUnavailable("service_unit_path_invalid") from None
    if (type(text) is not str or not re.fullmatch(r"/[A-Za-z0-9_./-]+", text)
            or not Path(text).is_absolute()):
        raise ServiceUnavailable("service_unit_path_invalid")
    return Path(text)


def _outside_inputs(parent, roots, identities):
    if any(parent == root or root in parent.parents for root in roots):
        raise ServiceUnavailable("service_unit_destination_invalid")
    # A renamed native directory retains its recorded device/inode even though
    # its old configured path no longer names it. Do not add output there either.
    for ancestor in (parent, *parent.parents):
        info = ancestor.stat()
        if (info.st_dev, info.st_ino) in identities:
            raise ServiceUnavailable("service_unit_destination_invalid")


def render_systemd_unit(directory, output):
    """Write one new 0600 unit; never install, enable, start or signal a service.

    ServiceDirectory's observer mode does not take an SH lease, allowing this
    immutable configuration read while the foreground supervisor owns EX.
    """
    directory, output = _absolute_path(directory), _absolute_path(output)
    published = False
    try:
        with ServiceDirectory(directory) as service:
            manifest = service.manifest
            runtime, policy = manifest["runtime"], manifest["policy"]
            interpreter = _absolute_path(runtime["python"])
            repository = _absolute_path(runtime["repository"])
            service_path = _absolute_path(service.path)
            stop_seconds = policy["stop_seconds"]
            if type(stop_seconds) is not int or not 1 <= stop_seconds <= 60:
                raise ServiceUnavailable("service_unit_policy_invalid")
            roots = (service_path, Path(manifest["pair"]["account_path"]),
                     Path(manifest["pair"]["capture_path"]))
            identities = {tuple(manifest["pair"][name + "_binding"]["directory"][:2])
                          for name in ("account", "capture")}
            _outside_inputs(output.parent.resolve(strict=True), roots, identities)

            def validate_destination(parent_observation):
                service.check()
                _outside_inputs(Path(parent_observation[0]), roots, identities)

            timeout = 2 * (stop_seconds + 5) + 5
            body = (
                "[Unit]\n"
                "Description=Supervised local PAPER account\n"
                "\n"
                "[Service]\n"
                "Type=simple\n"
                f"ExecStart={interpreter} -I -B -u {repository}/tools/paper_service.py "
                f"run {service_path}\n"
                "Restart=no\n"
                "KillMode=mixed\n"
                "KillSignal=SIGTERM\n"
                "SendSIGKILL=yes\n"
                "SuccessExitStatus=130 143\n"
                "UMask=0077\n"
                "NoNewPrivileges=yes\n"
                "LimitCORE=0\n"
                f"TimeoutStopSec={timeout}\n"
            ).encode("ascii")
            publish_new_report(output, body, validate_destination=validate_destination)
            published = True
            # Publication already checks this after its final directory fsync.
            # Reconfirm before returning success while every source FD is held.
            service.check()
            return {
                "schema_version": 1,
                "source": "paper_service",
                "operation": "unit",
                "mode": "paper",
                "live_enabled": False,
                "status": "unit_generated",
                "exit_code": 0,
                "service_id": manifest["service_id"],
                "attempt_id": None,
                "attempt_number": None,
                "children": {},
                "config_sha256": service.config_sha256,
                "unit_sha256": hashlib.sha256(body).hexdigest(),
                "timeout_stop_seconds": timeout,
                "activation_performed": False,
                "boundaries": {
                    "data_readiness": "not_assessed",
                    "live_execution": False,
                    "automatic_restart": False,
                    "service_activation": False,
                },
            }
    except ServiceStorageError:
        raise
    except (FileExistsError, FileNotFoundError, NotADirectoryError) as error:
        if published:
            raise ServiceStorageError("service_unit_output_unconfirmed") from error
        raise ServiceUnavailable("service_unit_unavailable") from error
    except OSError as error:
        raise ServiceStorageError("service_unit_output_unconfirmed") from error
    except (ServiceUnavailable, ValueError, TypeError, KeyError) as error:
        if published:
            raise ServiceStorageError("service_unit_output_unconfirmed") from error
        raise ServiceUnavailable("service_unit_unavailable") from error
