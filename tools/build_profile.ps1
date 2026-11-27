# Repository-owned compiler selection. Dot-source without installing anything.
function Resolve-DoujinBuildProfile {
    param(
        [Parameter(Mandatory)][string]$LockPath,
        [string]$MsvcToolsVersion,
        [string]$WindowsSdkVersion,
        [switch]$PinnedMsvcOnly,
        [switch]$UnqualifiedDevelopment,
        [switch]$HasLlvm,
        [switch]$HasExceptions
    )
    $lock = Get-Content -LiteralPath $LockPath -Raw | ConvertFrom-Json
    if ($lock.schema_version -ne 1 -or $lock.algorithm -ne 'DS-TOOLCHAIN-TREE-V1' -or
        $lock.qualification -notin @('candidate','approved') -or
        $lock.msvc_version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
        $lock.windows_sdk_version -notmatch '^10\.0\.[0-9]+\.[0-9]+$') {
        throw 'Invalid repository toolchain version policy.'
    }
    if ($PinnedMsvcOnly -and ($UnqualifiedDevelopment -or $HasLlvm -or $HasExceptions)) {
        throw 'Conflicting canonical and experimental compiler options.'
    }
    $canonical = !$UnqualifiedDevelopment -and !$HasLlvm -and !$HasExceptions
    if ($canonical) {
        if (($MsvcToolsVersion -and $MsvcToolsVersion -ne $lock.msvc_version) -or
            ($WindowsSdkVersion -and $WindowsSdkVersion -ne $lock.windows_sdk_version)) {
            throw 'Alternate compiler/SDK versions require -UnqualifiedDevelopment.'
        }
        return [pscustomobject]@{ name='canonical-msvc'; pinned=$true; msvc_version=$lock.msvc_version; windows_sdk_version=$lock.windows_sdk_version }
    }
    if ($HasExceptions -and !$HasLlvm) { throw 'MSVC architecture exceptions require an explicit LLVM configuration.' }
    return [pscustomobject]@{ name='unqualified-development'; pinned=$false; msvc_version=$MsvcToolsVersion; windows_sdk_version=$WindowsSdkVersion }
}
