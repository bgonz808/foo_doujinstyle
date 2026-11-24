param(
    [Parameter(Mandatory)][string]$SdkRoot,
    [Parameter(Mandatory)][string]$LLVMInstallDir
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root
if ($env:GITHUB_ACTIONS -ne 'true' -or ($env:GITHUB_REF -ne 'refs/heads/ci/invariant-cache-v0.0.226' -and ($env:GITHUB_REF -ne 'refs/heads/main' -or $env:GITHUB_EVENT_NAME -ne 'workflow_dispatch'))) {
    throw 'Dedicated isolated benchmark only; never use for production builds.'
}
$cache = Join-Path $env:RUNNER_TEMP 'invariant-cache-pilot'
if (Test-Path $cache) { throw 'Expected a fresh same-job cache' }
New-Item -ItemType Directory $cache | Out-Null
$sdk = (Resolve-Path -LiteralPath $SdkRoot).Path
$trees = [ordered]@{
    sdk = $sdk
    'sqlite-lib' = (Join-Path $root 'native/build/shared-lib/x64')
    'sqlite-obj' = (Join-Path $root 'native/build/shared-obj/sqlite/x64')
    'sqlite-generated' = (Join-Path $root 'native/build/generated/x64/sqlite')
}
$argsCommon = @{
    SdkRoot = $sdk
    Platform = @('x64')
    WindowsSdkVersion = '10.0.26100.0'
    LLVMInstallDir = $LLVMInstallDir
    LLVMToolsVersion = '23.1.6'
    X64WithMsvc = $true
    MsvcToolsVersion = '14.44.35207'
}
function Copy-Tree([string]$Source,[string]$Destination) {
    if (!(Test-Path -LiteralPath $Source -PathType Container)) { throw "Missing cache tree $Source" }
    & robocopy.exe $Source $Destination /E /COPY:DAT /DCOPY:DAT /R:0 /W:0 /MT:4 /XJ /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Robocopy failed: $LASTEXITCODE" }
}
function Manifest([string]$Directory,[string]$Output) {
    & py -3 tools/invariant_tree_manifest.py $Directory $Output
    if ($LASTEXITCODE -ne 0) { throw 'Cache manifest failed' }
}
$clock = [Diagnostics.Stopwatch]::StartNew()
& ./native/build.ps1 @argsCommon -VerifySharedSqliteCompilation
$clock.Stop()
Write-Host "CACHE-METRIC stage=cold_build seconds=$([Math]::Round($clock.Elapsed.TotalSeconds,3))"
$clock.Restart()
foreach ($name in $trees.Keys) {
    $dest = Join-Path $cache $name
    Copy-Tree $trees[$name] $dest
    Manifest $dest (Join-Path $cache "$name.json")
}
$clock.Stop()
Write-Host "CACHE-METRIC stage=snapshot_and_hash seconds=$([Math]::Round($clock.Elapsed.TotalSeconds,3))"
# Same runner, same absolute paths, same exact compiler and SDK. Preserve
# /Zi PDBs and tracking logs. Do not authorize reuse in any other context.
$clock.Restart()
foreach ($name in $trees.Keys) {
    Remove-Item -LiteralPath $trees[$name] -Recurse -Force
    Copy-Tree (Join-Path $cache $name) $trees[$name]
    $actual = Join-Path $cache "$name-restored.json"
    Manifest $trees[$name] $actual
    $expected = Join-Path $cache "$name.json"
    if ((Get-FileHash $actual).Hash -ne (Get-FileHash $expected).Hash) {
        throw "Restored cache tree does not match manifest: $name"
    }
}
$clock.Stop()
Write-Host "CACHE-METRIC stage=restore_and_verify seconds=$([Math]::Round($clock.Elapsed.TotalSeconds,3))"
foreach ($relative in @('native/build/obj/x64','native/build/publisher-obj/x64','native/build/x64')) {
    $dir = Join-Path $root $relative
    if (!(Test-Path -LiteralPath $dir -PathType Container)) { throw "Missing first-party directory $relative" }
    Remove-Item -LiteralPath $dir -Recurse -Force
}
$clock.Restart()
& ./native/build.ps1 @argsCommon -VerifyRestoredInvariantState
$clock.Stop()
Write-Host "CACHE-METRIC stage=warm_build seconds=$([Math]::Round($clock.Elapsed.TotalSeconds,3))"
# Libraries must remain byte-identical after linking the fresh first-party PE.
foreach ($name in @('sdk','sqlite-lib')) {
    $before = Get-Content -Raw (Join-Path $cache "$name.json") | ConvertFrom-Json -AsHashtable
    foreach ($file in $before.files | Where-Object { $_.path -like '*.lib' }) {
        $actual = Join-Path $trees[$name] $file.path
        if (!(Test-Path -LiteralPath $actual -PathType Leaf) -or
            (Get-FileHash -LiteralPath $actual -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.sha256) {
            throw "Invariant library changed: $name/$($file.path)"
        }
    }
}
$env:DOUJINSTYLE_TEST_PUBLISHER = (Resolve-Path 'native/build/x64/publish_references.exe').Path
& $env:DOUJINSTYLE_TEST_PUBLISHER --version
if ($LASTEXITCODE -ne 0) { throw 'Warm native worker failed' }
& py -3 -m unittest -v native.test_catalog_index_plan_ipc.NativeIndexPlanTests
if ($LASTEXITCODE -ne 0) { throw 'Warm native SQLite contracts failed' }
Remove-Item Env:DOUJINSTYLE_TEST_PUBLISHER
Write-Host 'CACHE-METRIC qualification=experimental_same_job_only'
