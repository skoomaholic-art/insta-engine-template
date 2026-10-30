# Security Policy

## Supported versions
Only the latest version on the `main` branch is supported. Update your copy from the template to get fixes.

## Reporting a vulnerability
Please **do not** open a public issue for security problems.

Report privately via GitHub: open this repository → **Security** tab → **Report a vulnerability**. Only the maintainer can see the report.
Include what is affected, steps to reproduce and the possible impact. You should get a reply within a few days.

## If your own keys leaked
1. Revoke and regenerate the key at its source (Telegram @BotFather `/revoke`, GitHub token settings, Meta app, Groq console).
2. Update it in Vercel environment variables or GitHub Secrets and redeploy.
3. If it was committed to git, rewriting history is not enough: the key must be revoked.

## Built-in protections
- The bot answers only `TG_OWNER_CHAT_ID`; Mini App requests are verified with Telegram's signed `initData`.
- Telegram updates require `TG_WEBHOOK_SECRET`; bot ↔ robot calls are HMAC-signed with `PIPELINE_SECRET`.
- Publishing requires two owner approvals and a username check (`IG_EXPECTED_USERNAME`).
