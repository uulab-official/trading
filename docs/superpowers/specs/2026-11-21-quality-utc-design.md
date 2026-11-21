# Required formatting checks and UTC compatibility

Date: 2026-11-21 (Asia/Seoul)

## Goal and authorization

The user asked to continue all remaining work after the paper lab was published.
This increment resolves the reported advisory Black/isort failures and deprecated
UTC clock usage, then verifies the published revision through the full CI matrix.
Broker credentials are absent; live broker validation remains a separate prerequisite.

## Decisions

- Preserve Python 3.10 runtime compatibility and existing stored timestamp formats.
  Public legacy naïve UTC datetimes remain naïve; strict checkpoint serializers
  continue to emit their existing explicit UTC offsets. Read the current clock with
  `datetime.now(timezone.utc)` and remove timezone metadata only at existing naïve
  compatibility boundaries.
- Fix the historical data loader's `utcnow().timestamp()` host-timezone error by
  converting aware UTC instants directly to epoch milliseconds.
- Format all Python files under `packages/`, `apps/`, and `tools/` with pinned
  Black/isort releases. Keep the existing 100-column and Python 3.10 style settings.
- Make both formatting checks required CI steps, including `tools/`. Keep the
  existing fatal flake8 selection required. No type-checking expansion is included.
- Pin direct test and lint dependencies in separate requirements files: tests run
  on Python 3.10–3.12; the lint environment runs on Python 3.11 or newer.
- Explicitly load the two pytest plugins used by the project and set async fixture
  loop scope. Incidental globally installed pytest plugins must not affect this
  repository's test behavior. Do not suppress project deprecation warnings;
  deprecated datetime UTC calls become test failures.
- Keep coverage artifacts required, Codecov opt-in, and image publication explicit.

## Verification

Behavioral regressions cover UTC midnight/daily loss, order timestamps, persisted
state/restart, direct strategy signal timestamps, and historical requests under
different host timezones. CI contract tests execute the actual lint scripts with
failing stub commands to ensure failures propagate and all source roots are checked.
After formatting, run the full pytest suite, the network-forbidden offline suite,
the synthetic paper lab, required lint, and syntax checks. A fresh independent
review precedes publication. Verify the exact published commit's Python matrix,
coverage artifacts, and Docker checks before reporting completion.

## Evidence-driven CI follow-up

The first published revision (`bd7ab91`) passed all Python matrix and formatter
jobs, but runner logs exposed forced Node20-to-24 migration in checkout,
setup-python, and cache actions. Use the latest maintenance release in the first
Node24-compatible major for the four actions used by required jobs: checkout
v5.1.0, setup-python v6.3.0, cache v5.1.0, and setup-buildx v4.4.1. Their official
manifests preserve the inputs consumed here. The observed hosted runner 2.338.2
exceeds their documented minimum 2.327.1. Existing publication conditions and
optional Codecov behavior stay intact; verify the actual final CI logs.

The same run reported missing README files referenced by four package metadata
files. Add the package READMEs rather than suppressing these warnings. Standard
setuptools editable-install informational notices are not application warnings
and do not justify changing the test installation mode.

## References

- https://docs.python.org/3.12/library/datetime.html
- https://black.readthedocs.io/en/stable/usage_and_configuration/the_basics.html
- https://pycqa.github.io/isort/docs/major_releases/release_policy.html
- https://docs.pytest.org/en/stable/how-to/plugins.html
- https://pytest-asyncio.readthedocs.io/en/stable/reference/configuration.html
