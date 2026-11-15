# PAPER host capability preflight (v50)

This standalone, standard-library-only utility checks whether a **Linux host**
exposes basic filesystem and IPC prerequisites for a supervised local PAPER
service. It **never** activates or configures a service, touches an account or
capture, calls a provider, or places an order. It does not establish production
readiness or validate failure recovery under realistic load.

## Run

Prepare a dedicated, empty, owner-controlled directory on the **actual target
host**, on the **intended local filesystem**. Never point this tool at an account,
capture or service directory. The directory must already exist, be an absolute
path, contain no symlink path components and have mode `0700`.

```bash
mkdir -m 700 /absolute/private/paper-host-probe
python3 -I -S -B tools/paper_host_preflight.py \
  --directory /absolute/private/paper-host-probe
```

On a verified extracted v49 runtime this new v50 tool is **not present**: the
v49 source-release profile is fixed and has not been expanded. Run the utility
from a reviewed v50 checkout. The probe makes ephemeral files only inside its
own temporary subdirectory; it removes that subdirectory after the checks.

The single JSON result includes `schema_version`, `source`, `status`,
`exit_code`, per-check status and explicit false readiness boundaries.

| Exit code | Meaning |
|---|---|
| `0` | All observed capabilities passed at this instant; not authorization to start |
| `1` | One or more supported-host checks failed/not checked |
| `2` | Invalid or unavailable operator-supplied probe directory / CLI |
| `4` | Result could not be delivered to stdout |

Checks: Linux, CPython 3.10+, `/proc/self/fd`, owner-only file creation and
no-follow refusal, advisory flock exclusion, local AF_UNIX stream socket, and
SQLite WAL transaction commit/readback. This is **capability sampling**, not an
exhaustive security audit, crash durability test, or guarantee of parent-death
signal/cgroup behavior. Probe outcomes can change after observation.

## Next operator gates

After a successful observation, separately verify the existing service's
quiescent preflight, true Unix-socket progress challenge, parent-death cleanup,
process-manager isolation, signal handling, manual re-entry policy and real
provider/clock/disk failure behavior on the chosen host. Use
[PAPER_SERVICE.md](PAPER_SERVICE.md) and
[PRODUCTION_CHECKLIST.md](../PRODUCTION_CHECKLIST.md); leave
`is_production_ready()` false until all relevant operational and broker gates
have independent evidence. Do not interpret CI runner success as a certificate
for another host or filesystem.
