# Contributing

Thanks for wanting to improve Instagram Manager. This repository is the **rendering and publishing engine (GitHub Actions)** half of the project; the other half is [assistant-bot-template](https://github.com/skoomaholic-art/assistant-bot-template).

## Before you start
- **Never commit secrets.** Tokens and keys belong only in Vercel environment variables and GitHub Secrets. If a key leaked in a commit, revoke it first, then rewrite history.
- Keep the core rule intact: **nothing is published without the owner's two approvals** (text and final). Pull requests that weaken this will not be merged.
- Free tiers only: no paid services or paid APIs as a requirement.
- No scraping of sites that forbid it; use official APIs.

## How to propose a change
1. Fork the repository and create a branch: `fix/short-name` or `feature/short-name`.
2. Keep the change small and focused, one topic per pull request.
3. Run the tests:
   ```
   PYTHONPATH=src python -m unittest discover -s tests -v
   ```
4. Open a pull request and fill in the template: what changed, why, how you tested it.

## Reporting bugs and ideas
Use the issue templates. For setup problems, first check the **Troubleshooting** table in the README.
For security issues, do **not** open a public issue: see [SECURITY.md](SECURITY.md).
