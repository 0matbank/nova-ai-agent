# Nova AI — Personal AI Agent OS

Remotely controllable (Telegram primary, WhatsApp secondary), multi-agent,
multi-skill, permission-controlled personal agent for a Windows PC.

The full specification is [docs/PLAN.md](docs/PLAN.md) — it is the single
source of truth. Build progress is tracked in [docs/PHASES.md](docs/PHASES.md).

## Layout

```
D:\Personal-Agent\
├── app\            ← this private GitHub repo (code, agents, skills, tests, docs, config templates)
├── data\           ← local only: agent.db, projects, task-history, indexes
├── secrets\        ← local only: .env / vault (user-only ACL)
├── sessions\       ← local only: browser + whatsapp sessions
├── workspace\  downloads\  logs\  backups\  local-models\
```

## Secrets boundary

- Real secrets live only in `D:\Personal-Agent\secrets\` — never inside `app\`.
- The repo contains only [.env.example](.env.example).
- `.gitignore` is a second line of defence; `python scripts/verify_foundation.py`
  checks the boundary and scans committable files for token patterns.
