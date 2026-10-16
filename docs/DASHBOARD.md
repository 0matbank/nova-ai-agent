# Local dashboard (Phase 24)

Plan §40: a visual management page on this PC, on the same engines as Telegram.

```
uv run python scripts/dashboard.py        # opens http://127.0.0.1:47810 in the browser
```

## Views

Overview (health, PC resources, active tasks, pending approvals, new task, queue
pause/resume, lockdown) · Tasks (history, steps, cancel) · Approvals (approve / reject,
history with who decided) · Providers & Models (provider health, circuit breakers,
models loaded in Ollama, aliases) · Agents · Skills (every tool with its permission
level) · Memory (project memory) · Schedules (cancel) · Logs (audit log) · Settings
(read-only).

## Same rules as Telegram

- A task typed here is a normal task (its messages also go to Telegram).
- Approve/Reject uses the same single-use Approval Engine (`decided_by: dashboard`);
  admin actions are still re-checked by the Privileged Broker; nothing here can skip
  a permission level.
- Settings are read-only: they live in `config/*.yaml`; secrets only in
  `secrets/.env` and are never shown.

## Protection

A web page on the PC can be reached by every website the owner visits, so:

- bound to 127.0.0.1 only; the Host header must be `127.0.0.1`/`localhost` (blocks DNS
  rebinding)
- every API call needs the dashboard token as a Bearer header — the link carries it in
  the URL fragment (`#token=…`, never sent to a server), the page keeps it for that tab
  only; a cross-site page cannot read it (no CSRF)
- CSP self-only, no framing (clickjacking), no CORS, `no-store`, no external scripts
  or fonts
- Nova's own browser refuses localhost URLs, so a web page Nova reads can't steer it here
- token: `secrets/dashboard.token`; new one: `scripts/dashboard.py --rotate`

## Development

React + Vite in `dashboard/` (exact versions, `package-lock.json`). After changing the
UI: `cd dashboard && npm ci && npm run build` — the built `dist/` is committed and
served by core (no Node needed at runtime).
