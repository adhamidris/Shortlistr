# Contributing

Issues and pull requests are welcome. Use synthetic profiles and local fixtures;
never attach CVs, tokens, cookies, real inbox content or application history to issues.

## Changes

- Keep candidate policy in profile/preferences, platform selection in targets and
  browser mechanics in the adapter. Don't hardcode a contributor's preferences.
- Preserve receipt/idempotency checks, pacing, cancellation cleanup and client timeout alignment.
- Test errors and recovery, not just the happy path. No live application submissions in tests.
- Update the owning documentation rather than copying rules into multiple files.
- Run `python -m unittest discover -s agent/tests -v` and `python agent/scripts/release.py --check`.

## Publishing checklist (maintainer)

1. Export using `release.py`; it copies only public source/docs/templates, never a
   working profile, generated config, runtime data or previous Git history.
2. Inspect the archive, license and tests before creating a public GitHub repository.
3. Use a fresh Git history for this edition. Do not upload the original personal repo.
4. Limit write access to trusted maintainers. Public visibility lets others read/fork
   and propose pull requests; it does not give them permission to push to your repository.
5. In GitHub **Settings → Rules → Rulesets**, protect the default branch: require a
   pull request and passing tests; block force pushes and deletion. Avoid requiring an
   approval from another person if you are the only maintainer—it can block your own merges.
6. Keep Actions read-only by default. This workflow uses `pull_request`, not
   `pull_request_target`; do not give untrusted pull-request code repository secrets.
7. Record actual Windows/macOS/client checks in `docs/validation.md` before declaring support verified.

Repository creation, publication, collaborator access and branch protection are separate
GitHub actions; the local setup does not perform them.
