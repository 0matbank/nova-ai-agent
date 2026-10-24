# device skill

Direct device controls (plan §68, V1.2) — understood by fixed rules, no AI, through the
Desktop Worker (the owner's session).

| Tool | Action | What |
|---|---|---|
| `volume` | device.read 🟢 / device.control 🔵 | read, set 0–100, change ±N, mute/unmute — answers with before → after |
| `media` | device.control 🔵 | play_pause / next / previous / stop — the standard media key, whatever player is in use |
| `brightness` | device.read 🟢 / device.control 🔵 | external monitors via DDC/CI, laptop panels via WMI; refuses honestly when the screen does not allow it |

All are reversible and touch no file or app.
