# Closed market history implementation plan

> Use superpowers:executing-plans; continue the user's already approved inline workflow.

**Goal:** Public closed candles → immutable fixture → existing replay → source-aware UI.
**Architecture:** Isolated acquisition module, explicit CLI, no network in replay.
**Tech Stack:** Python >=3.10 standard library, existing ES-module UI/Node tests.
**Spec:** ../specs/2026-10-31-market-history-design.md

## Global constraints
Paper only, GET only, fixed Upbit endpoint, no credentials, no false performance.
Do not merge main. Preserve existing report version-1 compatibility.

## Review focus
Missing tail/internal bars; JSON NaN/duplicate keys; pagination nonprogress;
path overwrite/races; malformed or forged source metadata.

## Tasks
- [x] Test provider normalization, pagination, bounded HTTP failures, immutable writes;
      reproduce failures then implement packages/data/trading_data/upbit_history.py.
- [x] Test CLI validation and generic/offline replay boundaries; implement
      tools/import_upbit_history.py and preflight in tools/paper_replay_dashboard.py.
- [x] Test optional provenance report round-trip and invalid-source rejection;
      extend replay_report.py without changing legacy report identities.
- [x] Test source labels/invalid metadata in replay.mjs; no new order controls.
- [ ] Run local targeted Python/Node checks; add original-runner integration tests
      and CI matrix jobs, inspect remote checks after committing.
- [x] Document exact commands, provider references, evidence and remaining boundaries.

Ruling: a fresh sparse sandbox is used because git clone DNS failed; connector-read
source hashes are verified. Full original-engine testing belongs in GitHub Actions.
