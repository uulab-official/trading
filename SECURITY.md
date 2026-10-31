# Public repository security policy

This repository is public during development. Never commit broker API keys, account identifiers, access tokens, signing secrets, personal information, or production configuration.

## Credential handling
- Use environment variables or a trusted secret manager. Do not put broker credentials in browser JavaScript, static assets, sample files, CI logs, or test fixtures.
- Keep broker credentials scoped to the minimum permissions; disable withdrawal permissions when the provider supports it.
- Keep live trading disabled by default. No production credentials in CI.
- Before any broker integration, document key rotation, revocation, token refresh, rate limiting, and account separation.

## Exposure response
1. Revoke/rotate the credential at its provider immediately.
2. Inspect all commits, tags, forks, Actions logs, artifacts, caches and published images for exposure.
3. Remove the secret from active code and consider history rewrite only after coordinating with collaborators; rewriting history does not retract existing clones.
4. Verify that affected broker accounts and orders are safe.

## Before changing visibility
- Review all Git history, branches, releases, packages and Actions artifacts for secrets.
- Audit public licenses and attribution, dependencies and security advisories.
- Do not assume switching from public to private makes previously copied code private.

## Reporting
Report security issues privately to repository maintainers rather than posting credentials or exploit details in public issues.
