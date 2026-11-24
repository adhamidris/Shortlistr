# Telegram bridge

Optional Kimi-only client using local authenticated REST/WebSocket APIs and Telegram
HTTPS long polling. See [guided setup](../docs/telegram-setup.md). No public webhook
or Tailscale connection is required; the host computer must stay awake and online.

## Controls

| Command | Purpose |
| --- | --- |
| `/status`, `/usage` | Session health, context and account allowance |
| `/settings`, `/models` | Inspect settings and available models |
| `/model <alias>`, `/reasoning <level>` | Change an advertised option while idle |
| `/permissions` | Choose a supported permission mode |
| `/activity on`, `/activity off` | Temporary per-turn tool activity |
| `/stop`, `/compact` | Stop work or compact an idle session |
| `/sessions` | Deliberately switch project sessions |
| `/reply` | Retrieve the last completed response without rerunning the agent |
| `/file <path>` | Download an allowed project file |

Ordinary messages start a turn or steer the active one through Kimi's prompt API.
Only the configured numeric owner in their private chat is accepted. Approvals use
explicit buttons, not ordinary replies. The bridge never changes global model defaults.
Activity cards use actual tool events; raw reasoning, outputs and secrets are omitted.
Completed replies use Telegram-safe formatting; Markdown file links offer downloads.

## State and delivery

The bridge preserves offsets, prompt IDs, receipts and session binding in
`data/telegram/bridge.sqlite3`. It resumes after disconnects with bounded backoff.
Ambiguous delivery isn't blindly resent; `/reply` recovers text without a new agent turn.
Only completed canonical assistant items are relayed, avoiding streaming duplicates.
The first connection does not replay old conversation history.

Incoming files are limited to 20 MB, outgoing to 49 MB. Credentials, hidden files,
logs, database files and paths/symlinks outside the repo cannot be downloaded.
Incoming files are private data, never instructions to execute.

Logs rotate at 1 MB with three backups. They report event/error types without message
bodies or credentials. A process lock prevents duplicate pollers. The bridge doesn't
start a hunt, follow up on email or create a schedule on its own.

## Compatibility

Requires Kimi's canonical turns/interactions and WebSocket subscription protocol.
Check the installed server's `/openapi.json`, `/asyncapi.json`, `/api/v1/kap.md`, then
verify `/status`, a harmless reply and controls. Supported core clients do not imply
this Kimi-specific bridge works with Codex or Claude Code.
