# Private test app with a pop-up for the app-control view tests: a main window that,
# once shown, opens a dialog OWNED by it (how real apps show "update available" etc.).
# Read-only tests: nothing here is ever pressed by them.
param([Parameter(Mandatory = $true)][string]$Title,
      [string]$Buttons = 'Update Now|Remind me Later|Skip Version')
Add-Type -AssemblyName System.Windows.Forms
$form = New-Object System.Windows.Forms.Form
$form.Text = $Title
$form.Width = 640
$form.Height = 400
$form.StartPosition = 'CenterScreen'

$main = New-Object System.Windows.Forms.Button
$main.Text = 'Start Work'
$main.Top = 20
$main.Left = 10
$main.Width = 150
$form.Controls.Add($main)

$dialog = New-Object System.Windows.Forms.Form
$dialog.Text = "$Title Dialog"
$dialog.Width = 520
$dialog.Height = 200
$dialog.StartPosition = 'CenterParent'
$label = New-Object System.Windows.Forms.Label
$label.Text = 'There is a new update available:'
$label.Top = 15
$label.Left = 10
$label.Width = 480
$dialog.Controls.Add($label)
$left = 10
foreach ($name in $Buttons.Split('|')) {
    $b = New-Object System.Windows.Forms.Button
    $b.Text = $name
    $b.AccessibleName = $name
    $b.Top = 60
    $b.Left = $left
    $b.Width = 150
    $dialog.Controls.Add($b)
    $left += 160
}

$dialog.Owner = $form
$form.Show()
$dialog.Show()
[System.Windows.Forms.Application]::Run($form)
