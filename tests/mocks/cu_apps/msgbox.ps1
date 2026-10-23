# Private classic Win32 message box (#32770 dialog) for computer-use tests (plan §71).
param([Parameter(Mandatory = $true)][string]$Title)
Add-Type -AssemblyName System.Windows.Forms
$null = [System.Windows.Forms.MessageBox]::Show('Save changes to the project before closing?',
    $Title, 'YesNoCancel', 'Question')
