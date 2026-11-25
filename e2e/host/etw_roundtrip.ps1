# Test-only standalone native provider; uses installed pinned MSVC/SDK.
param([Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference = 'Stop'
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vs = & $vswhere -latest -version '[17.0,18.0)' -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if ($LASTEXITCODE -ne 0 -or !$vs) { throw 'VS2022 not available' }
$vcvars = Join-Path $vs 'VC\Auxiliary\Build\vcvars64.bat'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$work = Join-Path $env:RUNNER_TEMP ('doujin-etw-probe-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
$exe = Join-Path $work 'probe.exe'
$batch = Join-Path $work 'compile.cmd'
@"
@echo off
call "$vcvars" 10.0.26100.0 -vcvars_ver=14.44.35207
if errorlevel 1 exit /b 1
cd /d "$work"
cl /nologo /std:c++17 /EHsc /W4 /WX /DDOUJIN_E2E_TRACE=1 "$root\e2e\host\etw_probe.cpp" "$root\native\host_trace_etw.cpp" /Fe:"$exe" /link advapi32.lib
if errorlevel 1 exit /b 1
cl /nologo /std:c++17 /EHsc /W4 /WX "$root\e2e\host\etw_export.cpp" /Fe:"$work\etw_export.exe" /link tdh.lib advapi32.lib
exit /b %errorlevel%
"@ | Set-Content -LiteralPath $batch -Encoding Ascii
& cmd.exe /d /c $batch
if ($LASTEXITCODE -ne 0) { throw 'Native ETW probe compile failed' }
py -3 -m e2e.host.etw_roundtrip --probe $exe --exporter (Join-Path $work 'etw_export.exe') --output $Output
if ($LASTEXITCODE -ne 0) { throw 'Native ETW transport qualification failed' }

Copy-Item -LiteralPath (Join-Path $work 'etw_export.exe') -Destination $Output
Write-Host ('TDH exporter SHA256: ' + (Get-FileHash -LiteralPath (Join-Path $Output 'etw_export.exe') -Algorithm SHA256).Hash.ToLowerInvariant())
