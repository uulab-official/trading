# Cooperative paper-process shutdown (v36)

## Design
The explicit historical run and resume CLIs handle SIGTERM and SIGINT through
one process-local stdlib controller. The first signal schedules exactly one
asyncio cancellation; the Python signal handler never raises into SQLite or
execution arithmetic. Existing run/resume finalizers attempt a persisted halt
and release their leases. Additional handled signals do not cancel the cleanup
again. No file-based remote control, signal sender, scheduler, restart or live
provider is introduced.

Preserve original fingerprinted run/recovery/account modules and all default
execution results. Install handlers before constructing the operation coroutine;
restore prior handlers on every exit. Require main-thread, non-nested CLI use.
A requested stop cannot return a successful result even if cancellation is
suppressed or the coroutine finishes just before the callback. Cleanup failures
remain failures and retain the existing halt-unconfirmed exit 3.

SIGINT returns 130, SIGTERM returns 143. Fixed stdout error JSON identifies the
signal but does not claim the account is halted. Check the saved account and use
preflight before explicit recovery. The first signal wins; no automatic resume.
This is cooperative, not bounded-latency stopping: synchronous work must yield,
and a handler that suppresses cancellation may delay termination. SIGKILL,
physical power loss and signals before/after the guarded operation are not
covered. Repeated SIGTERM/SIGINT requests deliberately do not force exit.
A late signal can arrive after valid result publication; preserve and audit
existing artifacts instead of deleting or overwriting them.

## Review targets
Real process signals during saved fills and halt; repeated signals; cancellation
suppression; handler installation/restoration failures; signal during synchronous
work; existing economic parity and fingerprints; errors never become success.

## Usage
No new flags are required. With the existing repository packages installed:

```bash
python tools/paper_operate.py /path/to/history.json --resumable \
  --directory .private/run-001
# For a prior incomplete opted-in run, after inspecting its state:
python tools/paper_resume.py .private/run-001
```

Ctrl-C requests SIGINT. A process manager's SIGTERM requests the same cooperative
cleanup, with exit 143 instead of 130. Target only the known paper process. This
change sends no signals to other processes and creates no PID or command files.
For a stopped run, inspect `paper_preflight.py DIRECTORY`; any saved-fill readback
still requires the separate explicit `--reconcile-saved-fills` recovery option.
An UNKNOWN intent without a saved fill remains blocked, never blindly retried.

Runtime stdout remains one JSON document. A handled signal reports the existing
operation-specific interrupted error plus `signal` and
`account_state: inspect_saved_record`. It is not a completion or halt certificate.
Normal success and existing failure codes are unchanged. Before operation startup
and after handler restoration the process's original signal behavior applies.
SIGINT/SIGTERM are temporarily owned by these top-level CLIs only; library calls
remain unchanged. Worker threads and nested event loops are refused by the wrapper.

If handler restoration fails, restoration of the other handler is still attempted;
success is refused. A prior execution/cleanup failure is not masked by a secondary
restoration error. No new dependency, account migration, price feed, public
hosting or live order path is added. Existing recovery fingerprints are unchanged.

## Verification scope
Tests use actual subprocess SIGTERM/SIGINT with real PaperTradingSession SQLite
state. Covers saved fill before acknowledgement, pre-strategy stop, interruption
of resume, repeated mixed signals during async halt, failed halt, interruption
inside a real SQLite commit, full-report equality after explicit recovery and
no-repeat completion. Controller tests cover cancellation suppression, late signal,
handler install/restore failure and refusal before factory invocation.

Local test environment is Linux; remote CI checks its configured Python versions.
No physical power-loss, other-OS, maximum-load, live-provider or hard shutdown
latency guarantee is claimed. The existing public Python signal/asyncio contracts
are documented at https://docs.python.org/3.10/library/signal.html and
https://docs.python.org/3.10/library/asyncio-task.html .
