# Private UI Automation test window (WinForms). Tests drive ONLY this window,
# never the user's own apps, and close it by its own PID.
param([Parameter(Mandatory = $true)][string]$Title)
Add-Type -AssemblyName System.Windows.Forms
$form = New-Object System.Windows.Forms.Form
$form.Text = $Title
$form.Width = 420
$form.Height = 240
$form.StartPosition = 'CenterScreen'

$box = New-Object System.Windows.Forms.TextBox
$box.Name = 'NovaInput'
$box.AccessibleName = 'Nova Input'
$box.Text = 'existing text'
$box.Width = 360
$box.Top = 10
$box.Left = 10
$form.Controls.Add($box)

$status = New-Object System.Windows.Forms.Label
$status.Name = 'NovaStatus'           # AutomationId; its UIA Name follows .Text
$status.Text = 'idle'
$status.Top = 110
$status.Left = 10
$status.Width = 360
$form.Controls.Add($status)

$safe = New-Object System.Windows.Forms.Button
$safe.Text = 'Nova Safe Button'
$safe.AccessibleName = 'Nova Safe Button'
$safe.Top = 45
$safe.Left = 10
$safe.Width = 170
$safe.Add_Click({ $status.Text = 'safe clicked' })
$form.Controls.Add($safe)

$danger = New-Object System.Windows.Forms.Button
$danger.Text = 'Delete Everything'
$danger.AccessibleName = 'Delete Everything'
$danger.Top = 45
$danger.Left = 200
$danger.Width = 170
$danger.Add_Click({ $status.Text = 'delete pressed' })
$form.Controls.Add($danger)

[System.Windows.Forms.Application]::Run($form)
