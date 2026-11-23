param([Parameter(Mandatory)][string]$Output)
$ErrorActionPreference = 'Stop'
$ver = '14.44.35207'
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (!(Test-Path $vswhere)) { throw 'Missing vswhere' }
$install = @(& $vswhere -latest -products '*' -requires Microsoft.Component.MSBuild -property installationPath)
if ($install.Count -ne 1) { throw 'Ambiguous active VS installation' }
$vc = Join-Path $install[0] ('VC\Tools\MSVC\' + $ver)
if (!(Test-Path $vc)) { throw 'Pinned MSVC v14.44.35207 unavailable' }
$result = [ordered]@{ schema_version = 1; tools_version = $ver; component_id = 'Microsoft.VisualStudio.Component.VC.14.44.17.14.ARM64.Spectre'; architectures = [ordered]@{} }
foreach ($arch in @('x86', 'x64', 'arm64ec')) {
    $binArch = if ($arch -eq 'arm64ec') { 'arm64' } else { $arch }
    $tool = Join-Path $vc ('bin\Hostx64\' + $binArch + '\cl.exe')
    $link = Join-Path $vc ('bin\Hostx64\' + $binArch + '\link.exe')
    $libraryDir = Join-Path $vc ('lib\spectre\' + $arch)
    $crts = [ordered]@{}
    foreach ($name in @('libvcruntime.lib', 'libcmt.lib', 'libcpmt.lib', 'libucrt.lib', 'msvcrt.lib')) {
        $file = Join-Path $libraryDir $name
        $crts[$name] = if (Test-Path $file) { (Get-FileHash $file -Algorithm SHA256).Hash.ToLowerInvariant() } else { $null }
    }
    $row = [ordered]@{
        cl_sha256 = if (Test-Path $tool) { (Get-FileHash $tool -Algorithm SHA256).Hash.ToLowerInvariant() } else { $null }
        link_sha256 = if (Test-Path $link) { (Get-FileHash $link -Algorithm SHA256).Hash.ToLowerInvariant() } else { $null }
        spectre_dir_present = (Test-Path $libraryDir)
        runtime_library_sha256 = $crts
    }
    $result.architectures[$arch] = $row
    $available = @($crts.GetEnumerator() | Where-Object { $null -ne $_.Value }).Count
    Write-Host "MSVC $ver ${arch}: mitigated CRT libraries $available / $($crts.Count)"
}
New-Item -ItemType Directory -Force (Split-Path -Parent $Output) | Out-Null
$result | ConvertTo-Json -Depth 9 | Set-Content -LiteralPath $Output -Encoding utf8
Write-Host 'Pinned MSVC compiler/linker and Spectre CRT SHA256 inventory recorded'
