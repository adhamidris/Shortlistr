# Validation status

## Automated checks

The test suite covers collection/deduplication, form state and submission receipts,
cancellation, reporting, email scope and bridge formatting/delivery. Browser tests use
local fixtures in isolated Chrome; they do not log in or submit real applications.
The CI matrix runs Python 3.11 and 3.14 on macOS and Windows.

## Release verification

- Local macOS installation/reinstallation: passed on Python 3.14.5.
- Offline suite: 154 tests passed, including browser fixtures, lock contention, profile import,
  configuration merging, criteria-change detection, reinstall preservation and release exclusions.
- Stdio discovery: LinkedIn, Remotive, We Work Remotely, Greenhouse, Playwright and optional Outlook passed.
- Clean archive extraction and installation in a path containing spaces and `&`: passed on macOS.
- Dedicated blank browser: launch, ownership detection and CDP connection passed; only
  that test browser was stopped afterward. No accounts or real applications were used.
- Windows code paths: implemented; CI and an interactive Windows installation are not yet verified.
- Kimi/Codex/Claude configuration: generated from documented project formats; the chosen
  live client must rediscover its tools during onboarding. Not all three interactive clients
  have been exercised on a clean machine.
- Outlook and Telegram: optional onboarding paths provided; fresh-user OAuth consent and
  bot/session delivery must be verified by that user. Existing account credentials are never shipped.
- Live websites: can change or restrict access independently of passing fixture tests.

## Technical references

- [Kimi project MCP configuration](https://moonshotai.github.io/kimi-code/en/customization/mcp.html)
- [Claude Code MCP configuration and timeouts](https://code.claude.com/docs/en/mcp)
- [Codex MCP configuration](https://developers.openai.com/codex/mcp/)
- [Chrome dedicated debugging profile requirement](https://developer.chrome.com/blog/remote-debugging-port)
- [Microsoft desktop OAuth](https://learn.microsoft.com/en-us/entra/msal/python/getting-started/acquiring-tokens)

Verify against installed versions when these interfaces change; do not silently shorten
long-call timeouts or remove application receipt protection to make a test pass.
