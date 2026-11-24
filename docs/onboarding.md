# Agent-guided setup

Read this when setting up or repairing the project. Handle files and commands yourself.
Do not dump this guide into chat. Show only the current step and anything the user must do.
For commands below, `<python>` means `.venv/bin/python` on macOS or
`.venv\Scripts\python.exe` on Windows. Use native Python and Chrome on the same machine;
do not introduce WSL, containers or remote browsers during ordinary setup.

## 1. Check the machine

1. Identify OS, current project directory, Python 3.11+, Node.js LTS, Chrome and the chosen agent.
2. If missing, guide the user to [Python](https://www.python.org/downloads/),
   [Node.js LTS](https://nodejs.org/en/download) or [Chrome](https://www.google.com/chrome/).
   On Windows, enable Python's PATH option; reopen the terminal after installation.
   Verify executable versions. Use an installed Python 3.11+ rather than macOS system Python.
3. Run `python3 setup.py` on macOS or `py -3 setup.py` on Windows (adjust to the detected interpreter).
   It creates the virtual environment, installs dependencies and writes project-local MCP configurations.
   Reruns preserve profile files and back up existing client configuration before merging.
4. If no AI client is installed, help choose/install one from its official guide:
   [Kimi Code](https://moonshotai.github.io/kimi-code/),
   [Codex](https://developers.openai.com/codex/cli/),
   [Claude Code](https://code.claude.com/docs/en/quickstart).
   User signs into their own account. Check current official instructions before giving install commands.

## 2. Read first, then interview

Ask where their CV is, or accept an attached CV. Do not ask them to arrange directories.
Run `<python> agent/scripts/profile_tools.py import-cv <path>`; it preserves the original
and extracts reference text. Inspect extraction quality. If scanned or incomplete, use
available document/vision tools or request a readable export. Never infer missing content.

Populate `profile/identity.yaml` with supported facts and evidence references. Keep unknowns
explicit. Ask only unanswered essentials, grouped into a short interview:

1. **Work:** preferred roles, work they enjoy/want to avoid, seniority and employment types.
   Offer a few evidence-based role suggestions if the CV supports them; do not limit them to titles on their CV.
2. **Where:** current location, remote/on-site preferences, relocation, authorization,
   sponsorship needs and working-hour constraints.
3. **Practical:** contact details, availability, salary/currency and whether that is a firm floor,
   any dealbreakers, and whether job-site account creation is allowed.
4. **Optional:** Outlook verification access and/or Kimi Telegram bridge. Both may be skipped.

Do not turn the interview into a compulsory questionnaire of every possible screening question.
Unknown sensitive or job-specific facts can remain unknown and become `needs_input` if required.
Do not infer work authorization from nationality or location. Do not invent technical experience.

## 3. Confirm a short plan

Write `config/preferences.yaml`, `config/targets.yaml`, `profile/qa-bank.md` and runtime timezone.
Keep profile facts in the profile, assessment rules in preferences, searches in targets.
Recommend a small set of relevant primary/fallback keywords from their evidence and goals.
Explain any proposed threshold: 55/100 is a starting recommendation, not a copied personal policy.
No mandatory application count, title exclusion, salary floor or technology preference is inherited.
Fallback searches follow the user's chosen goal; don't invent a minimum quota.

Show a concise summary: roles, locations, important exclusions, keywords and run sequence.
State plainly: **“When you ask for a run, it will submit qualifying applications automatically
using your original CV. Missing information or blocked sites will be reported.”**
Ask for corrections/confirmation of those criteria, not a separate auto-apply opt-in.
After confirmation, run `<python> agent/scripts/profile_tools.py validate --confirm`.
Changes to the confirmed files invalidate that confirmation; reconfirm changed criteria briefly.

### Search locations

LinkedIn needs verified geography IDs, not guessed country names. `runtime.json` accepts
`linkedin_geo_ids` additions. Known IDs are bundled for six countries, not a restriction
on the user's aspirations. For another location, use LinkedIn's location selector after
manual login and read the resulting `geoId`; save the exact displayed location name and ID.
Verify the filtered search before enabling it. If unavailable, report the limitation;
never silently substitute a different country or insert the location as a keyword.
The current browser adapter expects English UI labels; help switch LinkedIn to English
if necessary. Explain that content and role preferences may still be in another language.

## 4. Open the correct browser

Run `<python> agent/scripts/browser.py login --platform linkedin`.
Tell the user: “Sign in in the Chrome window that just opened, then tell me you're done.”
They handle passwords, MFA and security checks themselves. Do not promise a dedicated
profile avoids challenges. The profile persists under `secrets/browser-profile/`.
The helper uses its own loopback port and refuses an unrelated running browser.
If that port is occupied, select a free loopback port in runtime.json and regenerate MCP configuration.
Never take over another project’s browser or kill their ordinary Chrome.

## 5. Verify, don't assume

1. Run `<python> agent/scripts/doctor.py` and `setup_project.py check-tools`.
2. Restart/reload the chosen idle client if needed. In Kimi/Claude, inspect `/mcp`; in Codex,
   inspect the active tools. Confirm LinkedIn search/apply and browser tools are actually available.
   A generated config or separate handshake is not proof the running agent loaded it.
   Guide any project-trust prompt. If the host asks repeatedly for routine tool calls,
   explain its project-specific permission controls; automatic applications do not
   bypass host permissions. Do not silently enable unrestricted global access.
3. Inspect the logged-in LinkedIn page with the available browser tools without opening an application.
4. Run one small search with `max_pages=1`, output `runs/setup-check/search.json`, and
   cache `data/setup-check/linkedin.jsonl`. Use a new output path on repeat tests.
   This deliberately partial test must not enter the production ledger/cache.
5. Verify results match the selected keyword/location/date; a login wall or 403 is not zero jobs.
6. Report “Ready” only for verified capabilities; otherwise list the specific missing step.
   Do not start a hunt until the user requests it.

## Recovery

- Logged out: reopen the same profile using `browser.py login`; user logs in, then verify again.
- Browser absent: `browser.py start`. Running but unresponsive: inspect state before restarting;
  preserve drafts and never delete lock/profile files to force another instance.
- MCP missing: check the executable/config/handshake, then reload the idle client and verify its tools.
- Timeout/disconnect: operation may still be running. Check locks, saved output and receipts;
  never launch duplicate searches or blindly repeat a submission. Don't shorten client timeouts.
- Cloudflare/403/429/CAPTCHA: stop that platform for the run. No bypass, repeated keyword retries,
  cookie cloning or pressure on the user. Other platforms may continue. Login recovery is not a bypass.
- Optional integration blocked: keep core hunting usable and state that integration is unavailable.
- Lost profile: explain that login must be repeated; never claim to restore cookies from the ledger.

Use `docs/outlook-setup.md` or `docs/telegram-setup.md` only if selected. Support is honest:
macOS can be checked locally; Windows requires its CI and a real interactive login check.
