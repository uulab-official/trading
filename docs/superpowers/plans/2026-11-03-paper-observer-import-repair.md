# Paper observer import isolation repair

> Execute inline with test-driven development; no independent reviewer tool is available.

**Goal:** Repair the committed v25 observer CLI's JSON error protocol without suppressing warnings or weakening its assertions.
**Architecture:** Resolve the existing trading_core public re-exports through module-level __getattr__; preserve names, objects, version and direct submodule imports. Read-only observation does not initialize configuration or notifications. Python 3.10+ standard-library mechanism (PEP 562).
**Spec:** docs/OFFLINE_CORE_IMPORTS.md
**Tech stack:** existing Python, unittest subprocess isolation, SQLite paper session. No new dependencies.

## Evidence / boundaries
The latest baseline is 5dcf1668ccd4f1074f89dacc981b7c49741bedb2. Remote Order Outcome Safety run 41562958661 failed the missing-file CLI test on Python 3.10 and 3.13. __init__.py eagerly imports telegram_notifier; that module emits an optional-dependency warning before the CLI error JSON. Earlier local lazy initializers hid this difference. Git clone fails DNS in this runtime: use mounted earlier sources plus exact connector-fetched files with Git-blob hash validation. Local support reconstruction is not full-repository evidence; remote CI is required separately.

## Global constraints / review focus
No real provider requests, secrets, order changes, main merge or deployment.
Preserve all 22 existing public symbols and __version__. Failed attribute imports propagate and are not cached.
Do not disable logging, catch dependency errors globally, remove tests or edit the failing assertion.
Do not claim UI/E2E or full-repo local coverage from focused tests.
Check plain import, introspection, from-import, concurrent resolution, explicit optional failures, and clean CLI JSON on both success/error.

## Tasks
- [x] Write isolated subprocess import and actual observer CLI regression tests; observe baseline failure.
- [x] Add minimal lazy re-export implementation; rerun all new tests.
- [x] Run existing paper session/admission/control/order/reconciliation tests using actual core modules; review diff.
- [ ] Publish focused changes to existing draft branch using expected head lease; inspect exact-commit CI.
- [ ] Package focused patch, verification evidence, and v25 run instructions; disclose remaining gaps.
