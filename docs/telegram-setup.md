# Optional Telegram bridge — Kimi only

Guide one step at a time. This connects a private Telegram chat to a local Kimi web
session. It does not connect Codex/Claude, schedule hunts or expose an incoming port.
The computer must be awake, online and running Kimi plus the bridge.

## 1. Prepare Kimi

Use the installed Kimi Code CLI's `kimi --version` and `kimi web --help` to verify
support. The bridge expects Kimi Agent Protocol canonical turns/interactions; legacy
Python kimi-cli versions may not implement it. Do not guess compatible endpoints.

From this project, start `kimi web --no-open --port 58627` in a terminal. Keep the
default loopback binding and authentication. If that port serves another workspace,
choose a free port; don't restart someone else's server. Open the local web UI using
the credentials Kimi provides and create/select a session for this project. User
handles AI account sign-in. Never expose the bearer token in chat or screenshots.

## 2. Create a bot

1. In Telegram, open the official [@BotFather](https://t.me/BotFather).
2. Send `/newbot`, choose a display name and then an available username ending in `bot`.
3. BotFather returns a token. Keep it private; do not paste it into an AI conversation.
4. In a visible local terminal, run `<python> agent/scripts/setup_telegram.py`.
   Paste the token into its hidden prompt. It checks the bot and asks you to open its
   link, send `/start`, confirm your private identity and choose the project's Kimi session.
   Pressing Enter in the hidden prompt is expected; the token does not appear on screen.

The agent may prepare/open that terminal, but must not capture its credential input.
If multiple people used the bot or it has a webhook, setup refuses to take it over.
Create a new personal bot. No third-party “what is my ID” bot is required.

## 3. Connect and test

5. Run `<python> agent/scripts/telegram_bridge.py` in a second terminal and leave it running.
6. In your bot chat send `/status`, then `Reply with READY only; do not run tools.`
7. Verify one reply arrives, `/status` shows the intended session, and stop/control works.
   Do not claim compatibility from HTTP connectivity alone. No job application is used to test setup.

`<python>` is `.venv/bin/python` on macOS or `.venv\Scripts\python.exe` on Windows.
The helper creates private config and credentials; do not share these files.
A bridge restart reuses its saved session and delivery state. Never delete that state
merely to reconnect. Kimi must also be running. Closing either terminal stops access;
no startup service or job schedule is installed by default. If the user requests
login-time startup, configure the OS service separately after verifying this foreground
flow; keep the same paths, owner, session and profile, and test it before claiming success.

## Recovery

- Unauthorized/expired token: verify the local token file; don't disable authentication.
- Kimi upgraded: check its published local `/openapi.json`, `/asyncapi.json` and
  `/api/v1/kap.md`. Stop if the protocol differs; don't simulate a terminal by typing into it.
- Telegram disconnect: bounded retry is built in. `/reply` retrieves the last completed
  response without rerunning work. Don't clear delivery receipts to force retries.
- Different session: use `/sessions` deliberately. The bridge does not automatically
  move to a new conversation after a tool/config change.

See [bridge behavior](../agent/telegram-bridge.md) for controls and file limits.
