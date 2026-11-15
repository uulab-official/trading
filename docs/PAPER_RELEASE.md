# Verified minimal PAPER runtime release (v49)

The release tool packages the supported Python PAPER capture, continuing account,
supervisor, backup and synthetic backup-drill commands as one deterministic source
archive. It preserves the repository layout those commands require. The legacy
Rust Docker image is a separate product path.

The archive contains only a fixed reviewed file profile. It contains no trading
stores, credentials, configuration secrets, repository history or test fixtures.
Its supported commands need the Python standard library; the CPython interpreter,
standard library, operating system and service manager remain external.

## Host and trust requirements

Use a compatible CPython 3.10+ environment on Linux with the POSIX filesystem,
SQLite, flock, `/proc/self/fd`, parent-death signals and private Unix sockets
required by [PAPER_SERVICE.md](PAPER_SERVICE.md). Isolated release CI covers
Python 3.10, 3.12 and 3.13. This source archive is not an interpreter/OS image or
a claim about the support lifecycle of every Python version.

Use the release verifier from a checkout you already trust. Obtain the expected
archive SHA-256 from trusted build/CI evidence. A digest provided by the same
untrusted party as an archive is not an authenticity check. An internal manifest
establishes consistency only; this release format does not sign artifacts.

Input files/directories must be operator-owned, canonical paths with no symlink
components. Source/archive files must be regular, single-link and not writable
by group/others. Output parents must exist and be operator-owned; extracted
trees are private. Keep code releases and all account/capture/service directories
separate. Verification reads the exact profile and refuses extra files.

## Build and record the digest

Run from the trusted compatible checkout:

```bash
python3 -I -S -B tools/paper_release.py build \
  --output /absolute/owned/artifacts/paper-runtime.tar
```

The archive name must be new. Its parent must already exist; no output is
overwritten. The one JSON result includes `archive_sha256`, `manifest_sha256`,
file count, payload bytes and the engine fingerprint for the current interpreter.
Retain the archive digest separately with its reviewed commit/CI record.

The build reads only the fixed profile. Source timestamps, checkout location,
output name and umask do not enter the archive. Identical payload bytes produce
identical uncompressed USTAR bytes. The manifest and all headers/padding are
canonical. It does not include a timestamp or claim a Git commit identity that
could differ from the actual source bytes.

## Check before extracting

Replace `EXPECTED_SHA256` with the 64 lowercase hexadecimal characters from the
trusted build record. It is mandatory, not read automatically from the archive:

```bash
python3 -I -S -B tools/paper_release.py check \
  /absolute/owned/artifacts/paper-runtime.tar --sha256 EXPECTED_SHA256
```

The verifier checks the external digest, canonical manifest, exact allowlist,
sizes, per-file hashes, USTAR metadata and zero padding. It rejects compressed
archives, unknown/duplicate members, links, special files, sparse/GNU/PAX
extensions, traversal, altered headers and trailing data. The total archive is
limited to 8 MiB, each payload to 1 MiB and the manifest to 64 KiB.
No archive source is imported or executed by this operation.

## Extract into a new private release directory

```bash
python3 -I -S -B tools/paper_release.py extract \
  /absolute/owned/artifacts/paper-runtime.tar --sha256 EXPECTED_SHA256 \
  --directory /absolute/owned/releases/paper-v49

python3 -I -S -B tools/paper_release.py verify \
  /absolute/owned/releases/paper-v49 --sha256 EXPECTED_SHA256
```

The destination parent must exist. The destination itself must not exist, even
as an empty directory. The entire archive is checked before creating that leaf.
Extraction writes only fixed validated members using retained directory handles,
0700 directories and 0600 files. It writes the manifest last and confirms file,
directory and parent syncs plus original archive/tree bindings before success.

`verify` checks the exact private tree and reconstructs the canonical archive
identity against the supplied expected digest. It performs no installation,
imports, manager calls or account changes. Read-only verification establishes
current observed bytes; it does not prove a past operation's output delivery or
disk durability after a power failure. APIs close their descriptors on return;
no atomic check-to-later-execution guarantee is claimed.

Always use `-B` for commands in an extracted release so Python does not add
`__pycache__` files. Keep generated evidence/stores/unit files outside it.
Unexpected files, changed modes, missing members or altered contents make a
later exact-tree verification fail.

## Using an extracted runtime

Use the extracted tools at their fixed paths with the compatible interpreter:

```bash
python3 -I -S -B /absolute/owned/releases/paper-v49/tools/paper_stream.py \
  audit /absolute/private/account

python3 -I -S -B /absolute/owned/releases/paper-v49/tools/paper_service.py \
  configure /absolute/private/account --directory /absolute/private/new-service
```

The second command performs preflight/configuration only. Run/stop/health and
inactive systemd unit generation follow [PAPER_SERVICE.md](PAPER_SERVICE.md).
Actual collection starts only through the existing explicit native/service run
commands and then uses their existing public provider transport.

Preserved source bytes keep the native engine fingerprint compatible for the
same Python implementation and major/minor. Account and capture physical paths,
source ownership, terminal errors and full replay remain authoritative. Moving a
service configuration between runtime directories or interpreters is refused.
Create a new service configuration after inspecting the old one; never edit its
runtime metadata to force acceptance. Release extraction does not restore,
rebind, migrate or transfer authority for any trading account.

An explicit synthetic offline drill is available in the artifact:

```bash
python3 -I -S -B /absolute/owned/releases/paper-v49/tools/paper_backup_drill.py \
  --directory /absolute/private/new-synthetic-drill
```

It creates its own synthetic stores, forbids network and accepts no existing
account. Its result is controlled compatibility/recovery evidence. Real provider
soak, host service activation, external backup media and disaster recovery remain
separate operational work.

## Failures and retained output

| Exit | Meaning |
|---|---|
| 0 | The specified build/check/extract/verify operation completed |
| 2 | Invalid/unavailable input, expected digest/profile mismatch or refusal before possible output |
| 4 | Possible output, filesystem publication or final JSON delivery is unconfirmed |
| 130 / 143 | Requested interruption; retain any possible output and inspect before another attempt |

Every command returns one JSON result where stdout is usable. Errors use fixed
private labels. A broken/full stdout returns 4 and the fixed stderr label
`paper_release_output_unconfirmed` where possible. No command retries itself.

Preserve partial extracted directories and any named archive after uncertainty.
Do not overwrite, clean or reuse them automatically. A later successful `verify`
is a new observation of current bytes, not retroactive confirmation of the prior
process's output, cleanup, fsync or physical-storage guarantees.

## Delivery evidence and limits

The release CI builds twice, compares bytes and checks/extracts away from the
checkout. It imports every packaged Python module and all seven entrypoints with
`-I -S -B`, checks module origins against the extracted tree and proves a missing
module cannot fall back to the checkout or installed packages. Native account
audit, backup/recovery and synthetic drills also run with site-packages disabled.
The existing guarded service suite runs from a separate test harness against the
extracted code, including its fixed production worker argv (`-I -B -u`). Synthetic
transport and network denial belong to the tests, not to release CLI options.
Actual Unix-socket coverage is required on the Linux CI jobs. The CI archive's
own SHA-256 is the release-tool identity; the enclosing GitHub Actions artifact
download can have a different digest. CI retains the archive and digest for seven
days; an operator must retain reviewed evidence separately for longer use.

Preserve the reviewed commit, archive digest, CI run and chosen runtime/host
record together. CI source artifacts do not install, activate or publish a live
trading service. Live/data readiness remain unassessed, and the project's
production readiness remains false until its separate operating requirements
are implemented and verified.
