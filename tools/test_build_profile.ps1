$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'build_profile.ps1')
$lockPath = Join-Path $PSScriptRoot 'windows-toolchain-lock.json'
$lock = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json
function Assert-Throws([scriptblock]$Action) {
    $threw = $false
    try { & $Action | Out-Null } catch { $threw = $true }
    if (!$threw) { throw 'Expected invalid build profile to fail.' }
}
$profile = Resolve-DoujinBuildProfile -LockPath $lockPath
if (!$profile.pinned -or $profile.msvc_version -ne $lock.msvc_version -or $profile.windows_sdk_version -ne $lock.windows_sdk_version) {
    throw 'Canonical defaults do not match repository lock.'
}
$explicit = Resolve-DoujinBuildProfile -LockPath $lockPath -PinnedMsvcOnly -MsvcToolsVersion $lock.msvc_version -WindowsSdkVersion $lock.windows_sdk_version
if (!$explicit.pinned -or $explicit.name -ne $profile.name) { throw 'Explicit/default profile mismatch.' }
Assert-Throws { Resolve-DoujinBuildProfile -LockPath $lockPath -MsvcToolsVersion '0.0.0' }
Assert-Throws { Resolve-DoujinBuildProfile -LockPath $lockPath -WindowsSdkVersion '10.0.0.0' }
Assert-Throws { Resolve-DoujinBuildProfile -LockPath $lockPath -PinnedMsvcOnly -HasLlvm }
Assert-Throws { Resolve-DoujinBuildProfile -LockPath $lockPath -PinnedMsvcOnly -UnqualifiedDevelopment }
Assert-Throws { Resolve-DoujinBuildProfile -LockPath $lockPath -HasExceptions }
$dev = Resolve-DoujinBuildProfile -LockPath $lockPath -UnqualifiedDevelopment -MsvcToolsVersion '0.0.0'
if ($dev.pinned -or $dev.msvc_version -ne '0.0.0') { throw 'Explicit development override not preserved.' }
$llvm = Resolve-DoujinBuildProfile -LockPath $lockPath -HasLlvm -HasExceptions
if ($llvm.pinned) { throw 'LLVM experiment incorrectly marked canonical.' }
Write-Host 'BUILD-PROFILE contracts passed'
