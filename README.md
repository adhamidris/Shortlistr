# Shortlistr

[![Tests](https://github.com/adhamidris/Shortlistr/actions/workflows/tests.yml/badge.svg)](https://github.com/adhamidris/Shortlistr/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Your AI agent finds relevant jobs, applies, and keeps a clear record.**

A local, configurable job-application toolkit for **Kimi Code, Codex and Claude Code**.
MCP tools handle repeatable collection and supported application forms. The agent
assesses fit, answers questions from your real experience and handles external forms.

## Start here

1. Download/extract this repository into your own folder, or clone it.
2. Open that folder in Kimi Code, Codex or Claude Code.
3. Say: **“Read AGENTS.md and help me set this up. Walk me through only what you need from me.”**

Have your CV ready. The agent installs dependencies, organizes your files, helps you
set job criteria and opens a dedicated Chrome window for you to sign in. You don't
need to edit configuration by hand. Setup does not start applying.

Once ready, say **“Run my job hunt.”** That run submits qualifying applications
automatically using your confirmed preferences and original CV. No schedule is enabled.

Need an AI client first? See [the installation guide](docs/onboarding.md#1-check-the-machine).
Your own AI account/plan is required. This project does not include model access.

## What is included

| Capability | How it works |
| --- | --- |
| LinkedIn search | Full descriptions, filters, pagination and persistent deduplication |
| LinkedIn Easy Apply | Inspect fields, fill a batch, advance and explicitly submit |
| Remotive / We Work Remotely | Public API/RSS collection with cached responses |
| Greenhouse | Employer-board collection and supported application forms |
| External applications | Agent-operated Playwright using the same Chrome profile |
| Application history | Append-only ledger, Markdown summaries and an Excel-readable CSV |
| Outlook — optional | Signup verification; application follow-up only when requested |
| Telegram — optional | Private conversation with your Kimi session and file delivery |

Wuzzuf is not included. No scraper can guarantee a website will allow access. Login
walls, anti-bot challenges and unsupported forms are reported, not bypassed or retried
indefinitely. Unknown answers stay unknown; the agent must not fabricate qualifications.

## Design

**Collect → deduplicate → assess → apply → record → report.**

Collection runs sequentially. Native application tools go first for efficiency;
external opportunities are still assessed by the same criteria. Tool results stay
compact while descriptions live in local files. Saved receipts prevent blind repeat
submissions after interruptions. Models retain judgment; scripts handle mechanical work.

Your CV, profile, credentials, browser state, reports and generated client settings
are private, ignored by Git and excluded from release exports. Each installation has
its own persistent browser profile and loopback endpoint. Ordinary Chrome is untouched.

## Platforms and requirements

- Python 3.11+, Node.js LTS and Google Chrome.
- macOS and native Windows setup paths; Windows does not require WSL.
- A local tool-capable Kimi Code, Codex or Claude Code installation.
- LinkedIn's current browser adapter expects English interface labels.
- Telegram requires compatible Kimi web APIs. Outlook currently supports personal Microsoft accounts.

See [validation status](docs/validation.md) for what is tested versus still awaiting
interactive platform verification. “Config generated” is not the same as “agent connected.”

## Project layout

```text
AGENTS.md / CLAUDE.md   Agent entry points
agent/                 Hunting skill, tool documentation, scripts and tests
docs/                  Setup, recovery and optional-integration guides
templates/             Blank profile and configuration templates
profile/ config/       Generated local candidate facts and preferences
secrets/ data/         Local credentials, browser state, caches and exports
runs/ ledger/          Search results, reports and application history
```

## Development

```bash
python3 setup.py
.venv/bin/python -m unittest discover -s agent/tests -v
```

On Windows use `py -3 setup.py` and `.venv\Scripts\python.exe`. Tests use local
fixtures/mocks, not real applications. Integration tests require installed Chrome.
See [CONTRIBUTING.md](CONTRIBUTING.md) for checks and publishing guidance.

## License

[MIT](LICENSE). This project is independent of the named platforms and AI clients.
