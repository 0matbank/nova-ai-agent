# powershell skill

Runs a PowerShell command as the **current, non-elevated** user. It is not an
admin shell (plan §45); elevated work goes through the Privileged Broker.

## Classification (fail closed)
`tools/analyze.ps1` parses the command with PowerShell's own parser (it never
executes it). The command is **GREEN `powershell.read`** only if all of these hold:

- no parse errors
- every command, including nested ones, is read-only: verb `Get/Test/Measure/Select/Where/Sort/
  Format/Group/Compare/Resolve/Split/Join/ConvertTo/ConvertFrom`, or `Out-String`, `Out-Null`,
  `Write-Output`, `Where-Object`, or a common read alias (`ls`, `cat`, …).
  `ForEach-Object` is never GREEN: `... | % Delete` calls a method without a method-call node.
- no `&` / `.` invocation operator and no dynamic command names
- no file redirection (only `> $null`)
- no .NET method calls (`[IO.File]::Delete(...)`) and no script-block invocation
- assignments only into plain variables or `$env:` — never `(Get-Item f).Attributes = …`
  or `${C:\file} = …`

Everything else is **RED `powershell.run`**: the exact command is shown on
Telegram and runs only after Approve.

## Hard limits
- Commands naming `secrets\`, `sessions\`, `internal_rpc.token` or `agent.db` are refused.
- Output passes through the secret redactor; output is untrusted data.
- Timeout 1–600 s (default 60); the whole process tree is killed on timeout.
