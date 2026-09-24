# Static analysis of a PowerShell command using PowerShell's own parser.
# Input: base64 (UTF-8) command text. Output: one line of JSON. Never executes it.
param([Parameter(Mandatory = $true)][string]$B64)
$ErrorActionPreference = 'Stop'
$text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($B64))
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($text, [ref]$tokens, [ref]$errors)

$commands = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true) |
    ForEach-Object { @{ name = $_.GetCommandName(); op = $_.InvocationOperator.ToString() } })
$redirections = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FileRedirectionAst] }, $true) |
    ForEach-Object { $_.Location.Extent.Text })
$members = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.InvokeMemberExpressionAst] }, $true)).Count
# Assignments are only safe into plain variables ($x = ...) or $env:.
# `(Get-Item f).Attributes = 'Hidden'` or `${C:\f.txt} = 'x'` change the system.
$badAssigns = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] }, $true) |
    Where-Object {
        $l = $_.Left
        if ($l -is [System.Management.Automation.Language.ConvertExpressionAst]) { $l = $l.Child }
        $plainVar = $l -is [System.Management.Automation.Language.VariableExpressionAst] -and
            ((-not $l.VariablePath.DriveName) -or ($l.VariablePath.DriveName -in @('env', 'variable')))
        -not $plainVar
    }).Count
$scriptInvokes = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.ScriptBlockExpressionAst] -and $n.Parent -is [System.Management.Automation.Language.CommandAst] -and $n.Parent.InvocationOperator -ne 'Unknown' }, $true)).Count

@{
    parse_errors       = @($errors | ForEach-Object { $_.Message })
    commands           = $commands
    redirections       = $redirections
    member_invocations = $members
    unsafe_assignments = $badAssigns
    script_invocations = $scriptInvokes
} | ConvertTo-Json -Depth 4 -Compress
