param([Parameter(Mandatory)][string]$Output)
$ErrorActionPreference = 'Stop'
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$install = @(& $vswhere -latest -products '*' -requires Microsoft.Component.MSBuild -property installationPath)
if ($LASTEXITCODE -or $install.Count -ne 1) { throw 'Visual Studio installation unavailable or ambiguous' }
$vc = Join-Path $install[0] 'VC\Tools\MSVC\14.44.35207'
$kits = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10'
$msbuild = Get-Command msbuild.exe -ErrorAction SilentlyContinue
$exe = if ($msbuild) { $msbuild.Source } else {
    & $vswhere -latest -products '*' -requires Microsoft.Component.MSBuild -find 'MSBuild\Current\Bin\MSBuild.exe' | Select-Object -First 1
}
if (!$exe -or !(Test-Path -LiteralPath $exe -PathType Leaf)) { throw 'MSBuild missing' }
# Same MSBuild selection as native/build.ps1. Include compiler backend DLLs,
# linker/librarian, headers, libraries, SDK tools and imported C++ targets.
$roots = @(
    ('msvc-hostx64=' + (Join-Path $vc 'bin\Hostx64')),
    ('msvc-include=' + (Join-Path $vc 'include')),
    ('msvc-lib=' + (Join-Path $vc 'lib')),
    ('sdk-include=' + (Join-Path $kits 'Include\10.0.26100.0')),
    ('sdk-lib=' + (Join-Path $kits 'Lib\10.0.26100.0')),
    ('sdk-bin=' + (Join-Path $kits 'bin\10.0.26100.0')),
    ('msbuild-bin=' + (Split-Path -Parent $exe)),
    ('msbuild-vc-targets=' + (Join-Path $install[0] 'MSBuild\Microsoft\VC\v170'))
)
$argsList = @('--output', $Output)
foreach ($root in $roots) { $argsList += @('--root', $root) }
& py -3 (Join-Path $PSScriptRoot 'toolchain_inventory.py') @argsList
if ($LASTEXITCODE) { throw 'Toolchain inventory failed' }
