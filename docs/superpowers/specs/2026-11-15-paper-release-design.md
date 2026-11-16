# Verified minimal PAPER runtime release (v49)

## Intent and decision

Deliver the actual Python PAPER runtime as a deterministic, bounded source
archive that can be checked against an externally trusted digest and extracted
without interpreting archive paths or running its code. Current Docker images
package the separate Rust server. Editable installs in existing CI do not prove
that the PAPER service works outside this checkout without extra dependencies.

The user repeatedly requested production-quality continuation. This increment
implements and verifies the release tool, publishes the tested feature tree and
updates the existing Draft PR. It does not activate services, merge main, contact
providers/brokers, use credentials, send notifications or use a browser.

### Approaches considered

1. **Source-layout-preserving release plus isolated verification — selected.**
   It retains the existing fixed entrypoint paths and all engine source bytes.
   An archive is useful for review and transfer, while the external interpreter
   and OS remain explicit dependencies.
2. Conventional wheel/zipapp or a new container image. The runtime currently
   locates its fixed worker and hashes source files relative to the repository
   layout. Flattening that layout adds compatibility risk; a container also
   needs its own host/path/volume and interpreter-image contract.
3. Durable service inspection or a host drill. Useful future increments, but
   neither supplies the actual Python distribution artifact. They remain
   separate from this release feature.

Selection is the implementer's decision under the user's continuing instruction.
A fresh architecture review independently recommends approach 1. A clean
`python -I -S` import probe already loaded the native service/account/backup
closure and reproduced the prior execution fingerprint without site-packages.
Full isolated lifecycle tests must establish the broader runtime claim.

## Scope and compatibility

- Preserve all 24 files in `paper_stream._engine_fingerprint()` byte-for-byte
  against `19f2f64767535ee473742d3b2d34bc4b7cd45543`.
- Preserve repository-relative source layout and existing six entrypoints:
  capture, stream, service, internal worker, backup, and synthetic backup drill.
- Add only the release tool/modules and an explicit operator guide to that
  supported subset. No live application, config CLI, observer/dashboard, test
  fixtures, secrets, `.git`, stores or arbitrary repository files are included.
- The supported operational scope uses CPython 3.10+ and Linux/POSIX facilities;
  no third-party Python dependency is required for the shipped commands.
  CI proves the extracted runtime on Python 3.10, 3.12 and 3.13. This does not
  package or authenticate the CPython binary, standard library or operating OS.
- Existing accounts/captures remain at their original physical paths and keep
  native replay/identity/terminal rules. Existing service configurations retain
  exact repository/interpreter bindings. A release at another location requires
  an explicitly new service configuration; extraction never rewrites one.

## Public operations

The trusted checkout's `tools/paper_release.py` accepts only these forms:

```text
build --output NEW_ARCHIVE
check ARCHIVE --sha256 EXPECTED_SHA256
extract ARCHIVE --sha256 EXPECTED_SHA256 --directory NEW_DIRECTORY
verify DIRECTORY --sha256 EXPECTED_SHA256
```

The expected SHA-256 is mandatory for every read/extraction operation: 64
lowercase hexadecimal characters, supplied from trusted build/CI evidence.
The archive's own manifest never acts as its authenticity anchor. There is no
URL, download, arbitrary command, alternate source root, install, start, force,
overwrite, cleanup, migration or live option. Library APIs accept `stop=None`
with the existing exact numeric intent contract: integer 0, 2 or 15, never bool.

Success emits exactly one bounded JSON result with schema_version=1,
source=paper_release, operation, status, exit_code=0, archive_sha256,
manifest_sha256, file_count, payload_bytes, current execution_fingerprint,
and explicit boundaries. Source integrity is `expected_digest_matched` for
check/extract/verify; build reports `digest_created` and does not imply trusted
provenance. Readiness/data readiness/provider authenticity are not assessed,
live execution and service activation are false, interpreter_packaged=false.
No private paths, arbitrary diagnostics or archive content enter public errors.

Exit 2: invalid/unavailable input, integrity mismatch, unsupported profile or
pre-output refusal. Exit 4: possible output or final publication/cleanup/output
delivery cannot be confirmed. Requested signals return 130/143 unless a higher
storage uncertainty occurs. Preserve all possible named output and partial
extraction; never retry automatically. A broken/full stdout yields fixed stderr
`paper_release_output_unconfirmed` where possible and exit 4 after cleanup.

## Format: paper-runtime-v1

Use an **uncompressed canonical USTAR archive**. It contains `paper-release.json`
first and then the exact allowlist in lexical order. There are no directory
members, links, special files, sparse/GNU/PAX extensions or compressed variants.
Each header is the exact `TarInfo` USTAR serialization for its fixed ASCII path,
regular type, size, mode 0600, uid/gid 0, empty owner/group/link names, mtime 0.
Payload padding is zero to 512-byte blocks. End markers and padding are zero to
the first 10,240-byte boundary containing at least two final zero blocks.
Extra trailing bytes, even extra zero blocks beyond this length, are invalid.

Bounds: archive <= 8 MiB; individual source/document <= 1 MiB; manifest <=
64 KiB; exact versioned member count, currently 55 payload files plus manifest.
Read at most the respective bound plus one byte before refusing oversize input.
No untrusted generic archive extraction or implicit decompression is used.
The reader compares each raw header against the canonical expected header,
then validates exact content size/hash and padding. It never follows member
names supplied by an unvalidated archive.

The manifest is canonical UTF-8 JSON: sorted keys, compact separators,
ensure_ascii=True, allow_nan=False, one final newline. Duplicate keys, floats,
non-finite values, booleans in integer fields, excessive integers, unknown
fields, unknown profile/paths and noncanonical bytes are rejected. Top-level:

```text
schema_version: 1
format: "uulab.paper-runtime"
profile: "paper-runtime-v1"
runtime: {implementation: "cpython", minimum_python: [3,10],
          platform: "linux", third_party_dependencies: [],
          interpreter_packaged: false}
entrypoints: the fixed seven tool paths (six existing plus release)
files: [{path, size, sha256}, ...] in exact allowlist order
boundaries: {data_included: false, service_activation: false,
             live_execution: false, authenticated_provenance: false}
```

No current time, absolute paths, random IDs, Git metadata or interpreter patch
version enters the archive; identical payload bytes yield identical artifacts.
Execution fingerprint is derived from the 24 exact member bytes and the current
Python implementation/major.minor, using the unchanged native hashing contract.
It is a compatibility observation, not permission to resume an account.

## Explicit payload profile

The format module defines `RELEASE_FILES`, `ENTRYPOINTS` and the 24-name engine
mapping as immutable tuples/constants. Its closure is these 40 existing files:

```text
apps/server/trading_bot/durable_paper_resume.py
apps/server/trading_bot/durable_paper_run.py
apps/server/trading_bot/local_report_io.py
apps/server/trading_bot/market_capture.py
apps/server/trading_bot/market_capture_store.py
apps/server/trading_bot/paper_backup.py
apps/server/trading_bot/paper_backup_io.py
apps/server/trading_bot/paper_checkpoint.py
apps/server/trading_bot/paper_runner.py
apps/server/trading_bot/paper_service.py
apps/server/trading_bot/paper_service_protocol.py
apps/server/trading_bot/paper_service_state.py
apps/server/trading_bot/paper_service_unit.py
apps/server/trading_bot/paper_service_worker.py
apps/server/trading_bot/paper_stream.py
apps/server/trading_bot/paper_stream_engine.py
apps/server/trading_bot/paper_stream_store.py
packages/core/trading_core/__init__.py
packages/core/trading_core/exchange.py
packages/core/trading_core/order_manager.py
packages/core/trading_core/order_reconciliation.py
packages/core/trading_core/order_state_store.py
packages/core/trading_core/paper_admission.py
packages/core/trading_core/paper_control.py
packages/core/trading_core/paper_evidence.py
packages/core/trading_core/paper_evidence_compare.py
packages/core/trading_core/paper_exchange.py
packages/core/trading_core/paper_session.py
packages/core/trading_core/paper_session_observer.py
packages/data/trading_data/__init__.py
packages/data/trading_data/_validation.py
packages/data/trading_data/candle.py
packages/data/trading_data/indicator.py
packages/data/trading_data/public_candle_transport.py
packages/data/trading_data/upbit_history.py
packages/strategy/trading_strategy/__init__.py
packages/strategy/trading_strategy/base.py
packages/strategy/trading_strategy/breakout_strategy.py
packages/strategy/trading_strategy/ma_strategy.py
packages/strategy/trading_strategy/rsi_strategy.py
```

Plus `apps/server/trading_bot/paper_release_format.py`,
`apps/server/trading_bot/paper_release.py`, all seven entrypoints under `tools/`,
`LICENSE`, and `docs/{MARKET_CAPTURE,CONTINUOUS_PAPER,PAPER_BACKUP,PAPER_SERVICE,PAPER_RELEASE}.md`.
The included legacy classes reachable within frozen modules do not add supported
live entrypoints. Expanding the payload is an explicit reviewed profile change.

## Filesystem and publication guarantees

Inputs are operator-owned, stable regular single-link files without group/world
write permission. Source and archive paths must be canonical absolute paths
after normalization with no symlink components. Pin directories and file
descriptors, use no-follow/nonblocking opens, and compare device/inode/type/
owner/mode/size/mtime/ctime against original observations before and after reads.
Retain original bindings through the last filesystem confirmation and API
return; do not close then trust a replacement name within an operation. Hash/read
in bounded chunks with stop checks.

The synchronous APIs return a completed point-in-time report and close every FD
before returning. The CLI then serializes that report; it does not reopen names
or claim that filesystem observations are held atomically through stdout delivery
or a later execution. A mutation after API return is outside that completed
observation. Broken/full stdout still overrides delivery to exit 4 with the fixed
stderr label, without repeating the operation or changing the stored bytes.

Build reads only the fixed `_ROOT` and allowlist; unrelated files are ignored.
Pin destination parent before source collection. Do not publish into the source
tree. The archive output is a new private regular file, no clobber, with file and
parent fsync and final source/destination rechecks. Existing private publication
helpers may be reused without weakening their contracts or changing frozen code.

Check pins the archive and parent through complete validation and final return;
it performs no extraction, imports from payload, account reads or writes.

Extract pins its new destination's parent **before archive verification**, then
creates only the exact fixed directories and files via held directory FDs.
Directories are 0700, files 0600/single-link. No preexisting leaf directory is
accepted, even empty. Keep created directory FDs until final confirmation, write
the manifest last, fsync files then directories bottom-up and parent, verify the
complete exact tree and original archive bindings before success. Never remove
partial directories or adopt a replacement after failure. Reject nesting or
aliasing with the archive or trusted source tree.

Verify is a read-only exact-tree check: exact expected directory/file inventory,
private modes, canonical manifest, payload digests and canonical reconstructed
archive SHA against the external expected digest. No caches, bytecode or trading
stores may be written inside an extracted tree. It confirms current observed
bytes, not a past publication's fsync outcome or authenticated authorship.

These descriptor checks detect observed substitution; they are not a sandbox
against a privileged process coherently rewriting all trusted inputs/evidence.

## Meaningful acceptance evidence

1. Two builds of identical source bytes are byte-identical despite different
   source mtimes, output names and process umask; external tar tooling can read
   the result. Only explicit payload members are present.
2. Invalid digest, tampering, missing/extra/duplicate/traversal/link/sparse/PAX/
   compressed/oversized/noncanonical archives fail before destination creation.
3. Source/archive/parent/tree substitution, hardlinks, same-size content changes,
   restrictive umask, short writes, fsync failure, final recheck failure and
   actual signals preserve fail-closed behavior and bounded private output.
4. The artifact is extracted outside the checkout and executes with `-I -S -B`,
   hostile PYTHONPATH/user-site assistance removed, and network denied internally.
   Native synthetic capture/account continuation/audit and backup/recovery drill
   exercise the real shipped code. Exact old/new fingerprint compatibility and
   unchanged existing account/archive bytes are checked.
5. Real isolated worker launch resolves the extracted fixed worker, preserves
   its guard, and cannot import dependencies from the checkout. Actual Unix
   service tests are required in Linux CI; local capability skips are explicit.
6. Moving a configured service to a different runtime root remains refused;
   extraction never rewrites manifests or promotes old backup execution authority.
7. Full prior suites, new release tests, independent review, exact-tree feature
   publication, final-head CI logs and guarded Draft metadata readback pass.

## Primary references

- Python tarfile/USTAR formats and extraction surface:
  https://docs.python.org/3.10/library/tarfile.html
- Python isolated mode and disabling site processing:
  https://docs.python.org/3.10/using/cmdline.html

No deployment, public provider soak, host-disaster restore, signed supply-chain
provenance, interpreter/OS reproducibility or live financial readiness follows
from this source release or its synthetic tests.
