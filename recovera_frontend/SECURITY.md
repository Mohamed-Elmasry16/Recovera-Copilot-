# Security

- Keep database URLs server-side only.
- Never commit `.env` or production credentials.
- Use a read-only Postgres role for dashboard reads.
- Rotate credentials if they were exposed in logs, screenshots, or chat.
