# Market paper handoff implementation plan

> Execute inline with superpowers:executing-plans and failure-first tests.

**Goal:** Run existing imported public history in a durable paper account while
preserving validated source bytes and metadata.
**Architecture:** New bounded source reader/orchestrator, small CLI; reuse original
import validator, run/recovery engine, no-clobber publisher and signal guard.
**Tech Stack:** Python 3.10+, stdlib, existing modules only.
**Spec:** docs/MARKET_PAPER_RUN.md

## Global constraints
Do not change fingerprinted execution files, account schema, original import
contract, default CLI semantics, existing accounts, main branch or live controls.
Six focused files; reconstructed support is excluded from remote delivery.

## Review focus
Future self-reported retrieval clocks; checksum forgery cannot prove authenticity;
file replacement/duplicate JSON; failure before activation; explicit recovery of
interrupted nested run without replaying saved effects.

## Tasks
- [x] Failure-first tests: inspect invalid/gapped/noncanonical source without effects;
  actual imported fixture -> original durable engine equality and audit; CLI channels.
- [x] Implement snapshot/manifest publication, original engine call and source binding.
- [x] Expose inspect/run CLI and v36 cooperative shutdown without new live options.
- [x] Review races/errors, run related regressions and independent patch apply.
- [ ] Push focused change on latest existing Draft branch; check actual commit CI.

## Decisions
Ruling: user repeatedly requested continuous implementation; no broker choice is
needed for an offline handoff, so proceed without another approval question.
Ruling: separate nested run preserves old v32 fingerprints and recovery manifests;
source verification does not authenticate upstream history.
Ruling: full clone failed DNS; use isolated scoped artifact reconstruction, record
support hashes and distinguish local related checks from remote full-branch CI.

## Review evidence
Inline review (no independent reviewer tool): two regression tests failed because
source checks ran before the final execution-input read, and copied-source
permissions were not rechecked. Final checks now follow binding validation and
check both private file signatures around reads. Both regressions pass.
A new importer integration test initially referenced processed_bars instead of
the existing bars_processed field; the fixture assertion was corrected, not the
production report schema. All 40 new cases then passed.
