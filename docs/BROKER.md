# Privileged/Admin Broker (Phase 22)

Plan §4, §17A, §45, §47: Nova does approved administrator work through a separate
broker — never through an unrestricted admin shell.

## How it works

```
phone → core (normal user) ──RED approval──► owner taps ✅
          │ admin skill: exact approved parameters
          ▼  named pipe \\.\pipe\personal-agent-broker (local only)
       broker (administrator, protected copy)
          1. token + protocol version
          2. operation on its allowlist? parameters valid? (own checks)
          3. owner's approval for exactly this request in the database?
             (same task, same fingerprint, decided ≤ 15 min ago, never used by the broker)
          4. runs a fixed program by absolute path with an argument list — no shell
          5. checks the result (service state, rule present, package listed …)
```

| Operation | Permission | Notes |
|---|---|---|
| status, service query, firewall list | GREEN | read-only |
| service start / stop / restart | `admin.service_control` RED | only `privileged_broker.services` |
| firewall add / remove | `admin.firewall` RED | only rules named `Nova AI - …`; port or range, TCP/UDP, in/out, block/allow |
| package install / uninstall | `admin.package_install` / `_uninstall` RED | winget, exact id, source `winget`; the approval shows name + publisher |
| PC restart / shutdown | `system.restart` / `system.shutdown` RED | always 1–60 min delay; "restart cancel" (GREEN) stops it |
| fixed tasks | `admin.task` RED | flush_dns, time_resync, defender_quick_scan, defender_update |

`/lockdown` also locks the broker (only "restart cancel" still works); `/lockdown off`
unlocks it. `/status` shows the broker line; 3 failed health checks in a row → a
CRITICAL message on Telegram.

## Protection

- Installed into `C:\Program Files\Nova AI\broker` (own Python copy + pywin32 +
  `nova_broker` + `policy.json`). Normal user programs cannot change it; the
  allowlist is fixed at install time — changing config/workers.yaml needs a reinstall
  (UAC).
- Started by Task Scheduler (`\Nova AI\Broker`, at logon, highest privileges, restart
  on failure). The task cannot be changed without administrator rights.
- Pipe: first instance only (`FILE_FLAG_FIRST_PIPE_INSTANCE`); the owner's normal
  session may only read/write — it may not create pipe instances (no name squatting);
  network logons denied; remote clients rejected.
- `winget` is the real `winget.exe` inside the admin-only WindowsApps folder, never
  the user-writable alias. Task programs must live under `%SystemRoot%` or
  `%ProgramFiles%`.
- Log: `C:\Program Files\Nova AI\broker\logs\broker.log` (every request, refusal and
  result; client process id).

Known limit (Windows design): code already running as the owner's normal account can
read the internal token and write Nova's database, so it could forge an approval
row. It still only reaches the allowlisted, parameter-checked operations above — never
a shell, never arbitrary programs.

## Commands

```
uv run python scripts/broker_install.py            # install / update (UAC: Yes)
uv run python scripts/broker_install.py --check    # verify everything, no changes
uv run python scripts/broker_install.py --uninstall
```

After installing set `privileged_broker.enabled: true` in config/workers.yaml and
restart core. Development without elevation: `uv run python
workers/privileged-broker/run.py` (admin operations answer NOT_ELEVATED).
