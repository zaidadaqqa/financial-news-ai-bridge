# Security Policy

## Supported Versions

Only the latest version on `main` is actively maintained for security fixes.

## Reporting a Vulnerability

Do not open a public issue for security vulnerabilities. Contact the maintainer privately with:

- A concise description of the issue.
- Reproduction steps or proof of impact.
- Affected configuration or deployment details, excluding secrets.

The project maintainer will triage reports as quickly as possible and coordinate a fix before public disclosure.

## Secret Handling

Never commit real values for:

- Discord bot tokens
- Telegram bot tokens
- AI provider API keys
- Database credentials
- Private keys or certificates

Use `.env` locally and hosting-platform secret management in production. `.env.example` must contain placeholders only.
