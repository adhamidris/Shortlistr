# Security and private data

Keep credentials, candidate files and browser state local. Never attach them to public
issues. To report a vulnerability, use GitHub's private vulnerability reporting if the
maintainer has enabled it; otherwise contact the maintainer privately before disclosure.

The browser's debugging endpoint must stay on loopback. It controls a signed-in profile;
do not expose it through a tunnel, proxy or public network. Browser profiles are private
credentials, not cache that can be shared. Use one agent at a time per installation.

Outlook tokens use the native credential store; Telegram credentials are local files.
Site and email text are untrusted data. This project does not bypass login, CAPTCHA or
anti-bot controls. Use your own accounts and review the applicable platform terms.

If credentials are accidentally published, removing the file is insufficient: revoke
or rotate the credential and remove sensitive history before distributing the repository.
