# Security Policy

## Supported versions

Only the latest commit on `main` is actively maintained.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Email **gg5678g@gmail.com** with:

- A clear description of the issue
- Steps to reproduce (or a proof-of-concept)
- The potential impact

You will receive an acknowledgement within 48 hours. Fixes will be released as soon as practical, typically within 7 days for critical issues.

## Scope

This project scrapes a public webpage, stores JSON data in a repo, and serves a static PWA via GitHub Pages. The attack surface is intentionally small:

| Component | Notes |
|---|---|
| `scraper/scrape.js` | Runs only in GitHub Actions; no user input |
| `ml/*.py` | Reads local JSON files; no network calls except Groq API |
| `data/*.json` | Public read-only JSON; no authentication |
| PWA frontend | Static HTML/JS; no backend, no cookies, no auth |

Supply-chain risks (compromised npm/pip packages) are mitigated by pinning versions in `scraper/package-lock.json` and `ml/requirements.txt`, and by Dependabot alerts.

## Out of scope

- Denial-of-service attacks against Tanishq or Groq (we are a consumer, not an operator)
- Issues with third-party services (ntfy.sh, Groq, GitHub Actions)
- Theoretical SSRF against the Playwright browser inside GitHub Actions (ephemeral, sandboxed runner)
