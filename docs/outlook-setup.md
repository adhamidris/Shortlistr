# Optional Outlook setup

For the setup agent: walk through one numbered step at a time. Read the screen/error
before prescribing another step. Never ask for a password, access token or client secret.
Core job hunting works without this integration.

## What this enables

Recent signup/login verification during an application, plus application-status
follow-up only when requested. Microsoft grants delegated `Mail.Read` (mailbox-wide
read permission); local tools limit scans to Inbox/Junk and selected message bodies.
It cannot send mail or mark messages read. This integration currently supports
personal Outlook/Hotmail Microsoft accounts, not Gmail or organizational-only accounts.

## Registration

1. Open [Microsoft Entra](https://entra.microsoft.com/) and sign in yourself.
2. Search for **App registrations**, open it, then choose **New registration**.
   If no directory/app-registration access is available, explain that limitation.
   Do not borrow a third-party client ID or imply a personal inbox guarantees access.
3. Name the app `My Job Assistant`. Choose the supported-account option including
   **personal Microsoft accounts**. Select **Register**.
4. Open **Authentication** → **Add a platform** → **Mobile and desktop applications**.
   Add `http://localhost` as a redirect URI and save. This is a public desktop client;
   no client secret is needed. Do not add a web-app secret.
5. Open **API permissions** → **Add a permission** → **Microsoft Graph** →
   **Delegated permissions**. Search for `Mail.Read`, select it and add the permission.
   Do not add `Mail.Send`, `Mail.ReadWrite` or application permissions.
6. Open **Overview**. Copy **Application (client) ID**. It is a public identifier,
   so the agent can accept it and write `config/outlook.json`:

   ```json
   {"client_id": "the-application-client-id"}
   ```

## Sign in and verify

7. The agent runs `<python> agent/scripts/outlook_mail.py login` in a visible terminal.
   You sign in and consent in Microsoft's window. Password/MFA stay there.
8. The agent runs `outlook_mail.py status`; verify the intended account. No need to
   read an email merely to prove access. Then run `setup_project.py configure --outlook`.
9. Reload the idle AI client and confirm Outlook tools are actually listed. Explain
   that consent may occasionally need renewal; `login` repairs it without clearing history.

`<python>` is `.venv/bin/python` on macOS or `.venv\Scripts\python.exe` on Windows.
Tokens are saved in the native credential store. A denied/locked store blocks access;
there is no plaintext fallback. Revoke the app from your Microsoft account to remove access.

If Microsoft's labels differ, verify its current instructions instead of improvising:
[MSAL interactive desktop sign-in](https://learn.microsoft.com/en-us/entra/msal/python/getting-started/acquiring-tokens).
