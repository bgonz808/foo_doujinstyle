# Synthetic WPF controls use framework UIA peers rather than WinForms HWND fallback.
$ErrorActionPreference='Stop'
Add-Type -AssemblyName PresentationFramework,PresentationCore,WindowsBase
$window=[Windows.Window]::new()
$window.Title='DoujinStyle synthetic UIA probe'
$window.Width=400
$window.Height=240
$panel=[Windows.Controls.StackPanel]::new()
$button=[Windows.Controls.Button]::new()
$button.Content='Synthetic inspect button'
[Windows.Automation.AutomationProperties]::SetAutomationId($button,'ProbeButton')
[Windows.Automation.AutomationProperties]::SetName($button,'Synthetic inspect button')
$edit=[Windows.Controls.TextBox]::new()
[Windows.Automation.AutomationProperties]::SetAutomationId($edit,'ProbeEdit')
[Windows.Automation.AutomationProperties]::SetName($edit,'Synthetic inspect edit')
$edit.Text='Synthetic non-secret input'
$secret=[Windows.Controls.PasswordBox]::new()
[Windows.Automation.AutomationProperties]::SetAutomationId($secret,'ProbePassword')
[Windows.Automation.AutomationProperties]::SetName($secret,'Synthetic password label')
$secret.Password='SYNTHETIC_PASSWORD_MUST_NOT_APPEAR'
[void]$panel.Children.Add($button)
[void]$panel.Children.Add($edit)
[void]$panel.Children.Add($secret)
$window.Content=$panel
[void]$window.ShowDialog()
