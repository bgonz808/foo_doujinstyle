# Synthetic UIA probe only; never a foobar2000 test or a user desktop inspector.
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
$form=[Windows.Forms.Form]::new()
$form.Text='DoujinStyle synthetic UIA probe'
$form.Name='DoujinProbeWindow'
$form.Size=[Drawing.Size]::new(400,240)
$button=[Windows.Forms.Button]::new()
$button.Name='ProbeButton'
$button.Text='Synthetic inspect button'
$button.AccessibleName='Synthetic inspect button'
$button.Location=[Drawing.Point]::new(20,20)
$button.Size=[Drawing.Size]::new(220,32)
$edit=[Windows.Forms.TextBox]::new()
$edit.Name='ProbeEdit'
$edit.AccessibleName='Synthetic inspect edit'
$edit.Text='Synthetic non-secret input'
$edit.Location=[Drawing.Point]::new(20,70)
$secret=[Windows.Forms.TextBox]::new()
$secret.Name='ProbePassword'
$secret.AccessibleName='Synthetic password label'
$secret.UseSystemPasswordChar=$true
$secret.Text='SYNTHETIC_PASSWORD_MUST_NOT_APPEAR'
$secret.Location=[Drawing.Point]::new(20,110)
$form.Controls.Add($button)
$form.Controls.Add($edit)
$form.Controls.Add($secret)
try { [void]$form.ShowDialog() } finally { $form.Dispose() }
