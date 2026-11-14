# Read-only paper run overview (v35)

Inspect 1–8 explicitly named local historical runs in one call. Route a run with
any result.json entry to the existing completion audit; otherwise use the existing
preflight. A marker is NOT completion proof. Preserve every configured row,
including missing, corrupt and unmarked runs. Never automatically resume, reconcile,
halt, repair or submit orders. No recursive directory discovery or runtime change.

Return per-run diagnostics and observation times, state counts and an explicit
all_completed flag. Compatible incomplete runs are not completed. Only counts are
aggregated: never sum account cash, currencies, returns or expected trades across
runs. Sources are inspected sequentially, NOT as one simultaneous portfolio
snapshot. Earlier observations may change before later ones finish. Process/owner
availability stays unobserved and resume_authorized/live_enabled remain false.

Explicit unique aliases (ASCII/Hangul letters, digits, underscore and hyphen,
1–40 characters) and unique absolute directories are required before inspection.
Limit eight runs; underlying 5000-bar and file limits still apply per run. Long
replays are explicit one-shot work, not a polling endpoint or background monitor.
Unexpected single-run failures retain an unavailable row with a fixed reason.
Cancellation interrupts the entire call without publishing a success report.

Optional --html publishes a NEW private standalone Korean report through the
existing no-clobber publisher. Source directories must not contain the output.
No JavaScript, external fonts, requests, live controls or web server are added.
HTML escapes all dynamic values; raw paths and exceptions are omitted. Account
identifiers may occur in diagnostic details: keep reports private. Checksums and
successful audits establish internal consistency, not authenticity or profitability.

CLI: python tools/paper_overview.py --run ma=DIR --run rsi=DIR [--html NEW_FILE]
Stdout is one JSON report (even with HTML output); stderr is diagnostics.
Exit 0: every observation either verified complete or compatible incomplete;
exit 4: at least one saved-fill acknowledgement needs explicit attention;
exit 2: any blocked/unavailable run or invalid arguments/output;
exit 3: HTML might have been published but confirmation failed; verify before retry;
exit 130: interrupted. Exit 0 does not authorize any execution.

## Example
```bash
python tools/paper_overview.py \
  --run ma=.private/ma-run \
  --run rsi=.private/rsi-run \
  --html .private/run-overview-001.html
```
Each source must be an existing opted-in historical run. Missing and corrupt runs
remain visible. The output directory must already exist with private permissions;
HTML contains private diagnostics and must not be publicly hosted. It is an
immutable observation, not a live page: create a new filename for a later check.
The command does not schedule another inspection.
