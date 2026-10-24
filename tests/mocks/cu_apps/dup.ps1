# Private test app (computer-use tests, plan §72 CU-3): two buttons with the SAME label,
# a status line, and a button that replaces the first "Save" with a NEW "Save" button in
# the same place. Never the owner's apps.
param([Parameter(Mandatory = $true)][string]$Title)
Add-Type -AssemblyName System.Windows.Forms
$form = New-Object System.Windows.Forms.Form
$form.Text = $Title
$form.Width = 520
$form.Height = 260
$form.StartPosition = 'CenterScreen'

$status = New-Object System.Windows.Forms.Label
$status.Name = 'Status'
$status.Text = 'idle'
$status.Top = 120
$status.Left = 10
$status.Width = 480
$form.Controls.Add($status)

function New-Save([int]$left, [string]$says) {
    $b = New-Object System.Windows.Forms.Button
    $b.Text = 'Save'
    $b.Top = 20
    $b.Left = $left
    $b.Width = 120
    $b.Add_Click({ $status.Text = $says }.GetNewClosure())
    return $b
}
$script:first = New-Save 10 'first'
$form.Controls.Add($script:first)
$form.Controls.Add((New-Save 150 'second'))

$swap = New-Object System.Windows.Forms.Button
$swap.Text = 'Swap'
$swap.Top = 20
$swap.Left = 300
$swap.Width = 120
$swap.Add_Click({
    $form.Controls.Remove($script:first)
    $script:first = New-Save 10 'replacement'
    $form.Controls.Add($script:first)
    $status.Text = 'swapped'
})
$form.Controls.Add($swap)

$box = New-Object System.Windows.Forms.TextBox
$box.Name = 'Box'
$box.AccessibleName = 'Notes'
$box.Top = 70
$box.Left = 10
$box.Width = 300
$form.Controls.Add($box)

[System.Windows.Forms.Application]::Run($form)
