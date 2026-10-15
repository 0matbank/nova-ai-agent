# git skill

Git for projects registered in `config/projects/` (plan §35, Phase 15). Never Nova's
own folders. Credentials come from git's own credential manager on this PC — Nova
never sees or stores a token or password. `GIT_TERMINAL_PROMPT=0`: never hangs on a
login prompt.

| tool | action | notes |
|---|---|---|
| `status` | git.read (GREEN) | branch, upstream, changed files |
| `diff` | git.read (GREEN) | stat + patch against a ref |
| `pull` | git.pull (BLUE) | fast-forward only, clean tree only — never merges or rebases |
| `commit` | git.commit (YELLOW) | on a `nova/...` branch only; safety check = no secret in the change (else approval); the original branch is restored afterwards |
| `push` | git.push (RED) | every push needs the owner's approval; `nova/...` branches only; never `--force`; verified by `ls-remote` |

Owner choice 2026-10-15: each task commits on its own branch (`nova/task-<id>`);
merging into main stays with the owner.
