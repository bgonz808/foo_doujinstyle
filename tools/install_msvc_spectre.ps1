param()
$ErrorActionPreference = 'Stop'
$components = @(
    'Microsoft.VisualStudio.Component.VC.14.44.17.14.ARM64.Spectre',
    'Microsoft.VisualStudio.Component.VC.Runtimes.ARM64.Spectre'
)
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$setup = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\setup.exe'
if (!(Test-Path $vswhere) -or !(Test-Path $setup)) { throw 'Visual Studio installer unavailable' }
$installPath = @(& $vswhere -latest -products '*' -requires Microsoft.Component.MSBuild -property installationPath)
if ($installPath.Count -ne 1) { throw 'Visual Studio installation ambiguous' }
$channel = @(& $vswhere -latest -products '*' -property channelId)
if ($channel.Count -ne 1) { throw 'Visual Studio channel ambiguous' }
$toolRoot = Join-Path $installPath[0] 'VC\Tools\MSVC\14.44.35207'
$spectreDir = Join-Path $toolRoot 'lib\spectre\arm64'
$required = @('msvcrt.lib','vcruntime.lib','msvcprt.lib')
$missing = @($required | Where-Object { !(Test-Path -LiteralPath (Join-Path $spectreDir $_) -PathType Leaf) })
if ($missing.Count -eq 0) { Write-Host 'Pinned ARM64EC-compatible /MD Spectre CRT candidates present; skipping installer'; return }
$auth = Get-AuthenticodeSignature -LiteralPath $setup
if ($auth.Status -ne 'Valid' -or $auth.SignerCertificate.Subject -notmatch 'Microsoft') { throw 'Visual Studio installer Authenticode invalid' }
Write-Host 'Provisioning both the pinned ARM64 Spectre tools and separate officially documented ARM64/ARM64EC Spectre runtime component'
$args = 'modify --installPath "' + $installPath[0] + '" --channelId ' + $channel[0] + ' --add ' + $components[0] + ' --add ' + $components[1] + ' --quiet --norestart'
$result = Start-Process -FilePath $setup -ArgumentList $args -Wait -PassThru
if ($result.ExitCode -ne 0) { throw "VS Spectre component installation failed with exit $($result.ExitCode)" }
foreach ($component in $components) {
    $seen = @(& $vswhere -latest -products '*' -requires $component -property installationPath)
    Write-Host "Installed Microsoft component ($component): $($seen.Count -eq 1)"
}
foreach ($a in @('arm64','arm64ec')) {
    $lib = Join-Path $toolRoot ('lib\spectre\' + $a)
    $names = @(Get-ChildItem -LiteralPath $lib -File -Filter '*.lib' -ErrorAction SilentlyContinue)
    Write-Host "Target ${a}: Spectre library directory populated=$($names.Count -gt 0) count=$($names.Count)"
}
# Report only versioned toolchain-relative library paths, never user paths.
$toolsDir = Join-Path $installPath[0] 'VC\Tools\MSVC'
$versions = @(Get-ChildItem -LiteralPath $toolsDir -Directory -ErrorAction Stop)
foreach ($directory in $versions) {
    $v = $directory.Name
    foreach ($layout in @('lib\spectre\arm64ec','lib\arm64ec\spectre',
                         'lib\arm64ec','lib\spectre\arm64')) {
        $candidate = Join-Path $directory.FullName ($layout + '\libvcruntime.lib')
        if (Test-Path -LiteralPath $candidate) {
            $hash = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant()
            Write-Host "Toolset $v library layout $layout : libvcruntime SHA256=$hash"
        }
    }
}
$stillMissing = @($required | Where-Object { !(Test-Path -LiteralPath (Join-Path $spectreDir $_) -PathType Leaf) })
if ($stillMissing.Count -ne 0) { throw 'Pinned ARM64 Spectre /MD CRT import libraries absent after Microsoft provisioning' }
Write-Host 'Pinned ARM64EC-compatible /MD Spectre runtime candidates provisioned; actual link and BinSkim still required'
