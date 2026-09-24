# terminal skill

Runs one program with an argument list — **never through a shell**, so `&&`,
pipes and redirection cannot be injected. Shell programs (`cmd`, `powershell`,
`bash`, `wsl`) are refused here; use the powershell skill.

GREEN `terminal.read` only for exact read-only invocations:

- `<python|py|node|npm|uv|git|pip|java|ffmpeg|ollama|gh|code|dotnet> --version`
- `git status|log|diff|show|rev-parse|ls-files|describe|blame|shortlog` (no `--output`)
- `whoami`, `hostname`, `systeminfo`, `tasklist`, `ver`, `getmac`, `nvidia-smi`,
  `driverquery` (no arguments), `ipconfig [/all]`, `where <name>`

Everything else is RED `terminal.run` and needs approval with the full command
shown. Output is redacted and treated as untrusted data.
