# search skill

Read-only file discovery (all GREEN, action `search`).

| Tool | What it does |
|---|---|
| `find` | files/folders whose name matches a glob (`*.pdf`) and/or contains text |
| `recent` | files modified in the last N hours, newest first |
| `duplicates` | groups of identical files (same size, then same SHA-256) |
| `secrets` | leaked keys/tokens/passwords (the log redactor's patterns) and secret files (`.env`, `*.pem`, `id_rsa` …) — file + line + masked line only, never the value |

Scans are bounded (max results, max files visited, time limit) and always skip
`secrets\`, `sessions\` and the agent database. Results are data only.
