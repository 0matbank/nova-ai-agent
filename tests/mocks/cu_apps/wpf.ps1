# Private WPF test app (computer-use tests, plan §71). Never the owner's apps.
param([Parameter(Mandatory = $true)][string]$Title)
Add-Type -AssemblyName PresentationFramework
[xml]$xaml = @"
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        Title="$Title" Width="520" Height="380" WindowStartupLocation="CenterScreen">
  <StackPanel Margin="12">
    <TextBlock Text="Project settings" FontSize="16"/>
    <TextBox Name="NameBox" Text="draft" Margin="0,8,0,0"/>
    <CheckBox Content="Keep a backup" Margin="0,8,0,0"/>
    <ComboBox Margin="0,8,0,0" SelectedIndex="0">
      <ComboBoxItem Content="1080p"/><ComboBoxItem Content="4K"/>
    </ComboBox>
    <ListBox Height="60" Margin="0,8,0,0">
      <ListBoxItem Content="First clip"/><ListBoxItem Content="Second clip"/>
    </ListBox>
    <StackPanel Orientation="Horizontal" Margin="0,10,0,0">
      <Button Content="Save" Width="90"/>
      <Button Content="Cancel" Width="90" Margin="8,0,0,0"/>
    </StackPanel>
  </StackPanel>
</Window>
"@
$window = [Windows.Markup.XamlReader]::Load((New-Object System.Xml.XmlNodeReader $xaml))
$null = $window.ShowDialog()
