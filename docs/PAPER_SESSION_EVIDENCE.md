# Portable paper-session evidence (v28)

## Design
Capture one validated SQLite row through the existing read-only observer, rather
than assembling independently refreshed pages. Preserve the complete account,
orders, fills, policy and control in one bounded JSON envelope. Checksums detect
accidental content inconsistency; they are not signatures or proof of provenance.

Verify the saved file without the source DB, reusing the original bundle/account
validator to reconstruct balances. Preserve UNKNOWN/SUBMITTING exactly; never
normalize orders, open a writer, restore an executable account or query a provider.
The report distinguishes capture time from verification time and uses saved marks,
not current prices. No dashboard/web write, live order or migration is added.

A new private output file is published only after complete serialization and fsync.
Never overwrite any existing target. Publication errors after a possible publish
must preserve the file and report that the caller should verify it. A process
crash can leave a private temporary file; no claim of physical-power-loss testing.

All existing account versions (1/2/3) remain unchanged. Optional revision/digest
preconditions bind an export to a displayed observation; neither may be supplied
alone. Export validity is not current account status or permission to trade.

## Commands
The existing project package setup is sufficient; use normal Python from the
repository root. Only explicit local files are accessed. No environment values
are interpreted as credentials or account settings.

```bash
mkdir -p .private
chmod 700 .private
# Use an EXISTING account, a NEW output name, and an existing private directory.
python tools/paper_evidence.py export \
  --database .private/paper-account.sqlite \
  --output .private/paper-check-001.paper-evidence.json

# Works without the source database. Prints a reconstructed summary, not all IDs.
python tools/paper_evidence.py verify .private/paper-check-001.paper-evidence.json
```

To bind an export to a displayed observation, supply BOTH `--revision <string>`
and `--digest <64 lowercase hex characters>`. A changed source is refused, not
silently replaced with a newer snapshot. Without anchors, the export captures one
currently readable validated row. A writer can commit again immediately afterward;
this does not turn the saved observation into current account state.

Successful commands print one JSON result to stdout and exit 0. The result has
`scope=offline_snapshot_not_current_account`, separate `captured_at` and
`verified_at`, original account/revision/digest, counts and saved-mark balances.
`captured_at` is envelope assembly time, not a broker/market timestamp. Source
UNKNOWN/SUBMITTING remains unchanged. Validation may succeed with unresolved
orders: it verifies consistency, not permission to resubmit or resume trading.

Exit 2 means unavailable/invalid input or output; exit 4 means a revision conflict.
Exit 3 (`paper_evidence_published_check_required`) means a complete output may
already exist, but publication/directory durability was not confirmed. Preserve
it and run `verify`; successful verification checks contents, not power-loss
persistence. Ctrl-C returns 130 and advises checking output before retrying.
Failures emit fixed JSON codes without raw filesystem errors or source paths.
There is no force, overwrite, restore, automatic retry or repair command.

## Format and privacy
The canonical UTF-8 JSON envelope contains exact `payload` from the validated
account row, source revision as a decimal string, source SHA-256, assembly time,
fixed paper/read-only flags and a SHA-256 of the envelope without its own digest.
It includes ALL existing records, not the dashboard's first 100. Original
quantity strings and order/fee timestamps remain unchanged. Format version is 1;
account versions 1/2/3 are accepted. Unknown fields, duplicates, nonfinite JSON,
invalid dates, noncanonical bytes and inconsistent accounting are rejected.
File size is bounded to the original 16 MiB payload allowance plus 4 KiB metadata.

Files are plaintext, created mode 0600 in an owner-only directory. `.private/` and
`*.paper-evidence.json` are Git-ignored as a convenience, not access control. Do not commit,
email or publicly upload unredacted exports. Credential fields, process secrets
and absolute source paths are not inserted, but user-chosen identifiers can
contain private information. The checksum is NOT a signature: someone able to
rewrite all contents and recompute hashes can fabricate another consistent file.
There is no assertion that a saved revision is the latest or externally genuine.

## Storage boundaries
Local POSIX filesystem only. Export uses a private same-directory temporary file,
flush/fsync, no-clobber hardlink publication, temporary unlink and directory fsync.
Existing regular files, symlinks and directories are never overwritten. A random
temporary-name collision never deletes another writer's file. A hard crash may
leave `.paper-evidence-*.tmp`; this is not a published success record.

The verifier rejects symlinks, public permissions, non-regular files, oversized
files and observed file replacement/change. Read-only hardlinks are permitted so
an output published immediately before process death can be verified even while
its orphan temporary link remains. Neither link is automatically removed. This
is not a defense against malicious same-user changes, network filesystems or a
claim that physical power-loss recovery has been tested.

## Verification
```bash
python -m unittest discover -s tests -p 'test_order_paper_evidence.py' -v
```
Tests cover original account roundtrips, all-record export, decimal/revision
precision, incomplete acknowledgements, versions/currencies, corruption with
recomputed checksums, source swaps, concurrent publishers, actual process death
before/after publication and clean CLI error channels. No new browser interface
or real provider connection is added in this increment.
