# app-control skill

Open and close desktop apps **inside the user session** via the Desktop Worker
(plan §4). Native process control — no mouse coordinates (plan §17).

| Tool | Action (level) | Verification |
|---|---|---|
| `open` | app.open (BLUE) | the app's process is running afterwards |
| `close` | app.close (BLUE) | graceful `WM_CLOSE`; reports if the app is still running (e.g. "save?" dialog) |
| `kill` | process.kill (RED) | process gone — unsaved work is lost, so approval is required |

Only apps listed in `apps.json` can be opened (add new ones there with their
executable candidates and process names). `explorer` (the Windows shell) can be
opened but never closed or killed.

If the Desktop Worker is not running (nobody logged in), the tool answers:
"এই কাজের desktop অংশ চালাতে user login / PC unlock দরকার" (plan §5).
