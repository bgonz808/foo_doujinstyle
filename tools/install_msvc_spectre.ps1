param()
$ErrorActionPreference = 'Stop'
$component = 'Microsoft.VisualStudio.Component.VC.14.44.17.14.ARM64.Spectre'
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$setup = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\setup.exe'
if (!(Test-Path $vswhere) -or !(Test-Path $setup)) { throw 'Visual Studio installer unavailable' }
$installPath = @(& $vswhere -latest -products '*' -requires Microsoft.Component.MSBuild -property installationPath)
if ($installPath.Count -ne 1) { throw 'Visual Studio installation ambiguous' }
$channel = @(& $vswhere -latest -products '*' -property channelId)
if ($channel.Count -ne 1) { throw 'Visual Studio channel ambiguous' }
$toolRoot = Join-Path $installPath[0] 'VC\Tools\MSVC\14.44.35207'
$spectre = Join-Path $toolRoot 'lib\spectre\arm64ec\libvcruntime.lib'
if (Test-Path $spectre) { Write-Host 'Pinned ARM64EC Spectre CRT present'; return }
$auth = Get-AuthenticodeSignature -LiteralPath $setup
if ($auth.Status -ne 'Valid' -or $auth.SignerCertificate.Subject -notmatch 'Microsoft') { throw 'Visual Studio installer Authenticode invalid' }
Write-Host "Provisioning missing pinned Visual Studio component $component"
$args = 'modify --installPath "' + $installPath[0] + '" --channelId ' + $channel[0] + ' --add ' + $component + ' --quiet --norestart'
$result = Start-Process -FilePath $setup -ArgumentList $args -Wait -PassThru
if ($result.ExitCode -ne 0) { throw "VS Spectre component installation failed with exit $($result.ExitCode)" }
if (!(Test-Path $spectre)) { throw 'ARM64EC Spectre CRT still missing after verified installation' }
Write-Host 'Pinned ARM64EC Spectre CRT provisioned and verified'
