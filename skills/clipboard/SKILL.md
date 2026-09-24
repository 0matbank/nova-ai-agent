# clipboard skill

| Tool | Action (level) | Notes |
|---|---|---|
| `get` | clipboard.read 🟢 | text only; **untrusted**, and passed through the secret redactor |
| `set` | clipboard.write 🔵 | verified by reading it back |

Runs in the Desktop Worker (the clipboard belongs to the user session).
