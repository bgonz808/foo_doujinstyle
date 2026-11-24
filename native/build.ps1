param(
    [Parameter(Mandatory)]
    [string]$SdkRoot,
    [ValidateSet('Win32', 'x64', 'ARM64EC')]
    [string[]]$Platform = @('Win32', 'x64', 'ARM64EC'),
    [ValidatePattern('^10[.]0[.][0-9]+[.][0-9]+$')]
    [string]$WindowsSdkVersion,
    [string]$LLVMInstallDir,
    [ValidatePattern('^[0-9]+[.][0-9]+[.][0-9]+$')]
    [string]$LLVMToolsVersion,
    [switch]$Arm64ecWithMsvc,
    [switch]$Win32WithMsvc,
    [switch]$X64WithMsvc,
    [ValidatePattern('^[0-9]+[.][0-9]+[.][0-9]+$')]
    [string]$MsvcToolsVersion,
    [switch]$E2ETrace,
    # Explicit pinned MSVC path, without an unused LLVM install.
    [switch]$PinnedMsvcOnly,
    [switch]$VerifySharedSqliteCompilation,
    # Experimental same-job restored state only; not a trusted remote cache.
    [switch]$VerifyRestoredInvariantState,
    [switch]$VerifyRestoredSqliteOnly,
    [ValidateSet(1,2,4)][int]$CompilerJobs = 2
)
$ErrorActionPreference = 'Stop'
$baseVersion = (Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot '..\VERSION')).Trim()
if ($baseVersion -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') { throw 'Invalid VERSION.' }
$identityJson = & py -3 (Join-Path $PSScriptRoot '..\tools\build_identity.py')
if ($LASTEXITCODE) { throw 'Could not determine build identity.' }
$identity = $identityJson | ConvertFrom-Json
$release = "v$($identity.version)"
$sdk = (Resolve-Path $SdkRoot).Path
$buildArgs = @('/p:Configuration=Release', "/p:FoobarSdkRoot=$sdk", "/p:DoujinCompilerJobs=$CompilerJobs")
if ($E2ETrace) {
    if ($Platform.Count -ne 1 -or $Platform[0] -ne 'x64') { throw 'E2ETrace is only supported for isolated x64 host tests' }
    $buildArgs += '/p:DoujinE2ETrace=true'
}
if ($MsvcToolsVersion) { $buildArgs += "/p:VCToolsVersion=$MsvcToolsVersion" }
if ($WindowsSdkVersion) {
    $buildArgs += "/p:WindowsTargetPlatformVersion=$WindowsSdkVersion"
}
if ($PinnedMsvcOnly) {
    if (!$WindowsSdkVersion -or !$MsvcToolsVersion -or $LLVMInstallDir -or $LLVMToolsVersion -or
        $Arm64ecWithMsvc -or $Win32WithMsvc -or $X64WithMsvc) {
        throw 'PinnedMsvcOnly requires exact MSVC/SDK pins and no LLVM/exception switches.'
    }
    $buildArgs += '/p:PlatformToolset=v143'
}
if ($LLVMToolsVersion -and !$LLVMInstallDir) {
    throw 'Specify LLVMInstallDir to pin a specific clang-cl version.'
}
if ($Arm64ecWithMsvc -and !$LLVMInstallDir) {
    throw 'Explicit ARM64EC MSVC exception requires pinned ClangCL for other architectures.'
}
if ($Arm64ecWithMsvc -and !($Platform -contains 'ARM64EC')) {
    throw 'Arm64ecWithMsvc requires the ARM64EC target in Platform.'
}
if ($Win32WithMsvc -and !$LLVMInstallDir) {
    throw 'Explicit Win32 MSVC exception requires pinned ClangCL for x64.'
}
if ($Win32WithMsvc -and !($Platform -contains 'Win32')) {
    throw 'Win32WithMsvc requires the Win32 target in Platform.'
}
if ($X64WithMsvc -and !($Platform -contains 'x64')) {
    throw 'X64WithMsvc requires the x64 target in Platform.'
}
if ($LLVMInstallDir) {
    $llvm = (Resolve-Path -LiteralPath $LLVMInstallDir).Path
    $clang = Join-Path $llvm 'bin\clang-cl.exe'
    if (!(Test-Path -LiteralPath $clang -PathType Leaf)) {
        throw "clang-cl.exe was not found at '$clang'. Pass the LLVM installation root."
    }
    $clangBanner = & $clang --version
    if ($LASTEXITCODE) { throw "clang-cl --version failed at '$clang'." }
    $clangVersion = [regex]::Match(($clangBanner -join "`n"), '\bclang version (\d+\.\d+\.\d+)\b')
    if (!$clangVersion.Success) { throw "Could not determine the clang-cl version at '$clang'." }
    $actualVersion = $clangVersion.Groups[1].Value
    if ($LLVMToolsVersion -and $LLVMToolsVersion -ne $actualVersion) {
        throw "Requested clang-cl $LLVMToolsVersion but '$clang' reports $actualVersion."
    }
    Write-Host "Using clang-cl $actualVersion at $clang"
    $buildArgs += @('/p:PlatformToolset=ClangCL', "/p:LLVMInstallDir=$llvm", "/p:LLVMToolsVersion=$actualVersion")
}
if ($VerifyRestoredSqliteOnly) {
    if ($env:GITHUB_ACTIONS -ne 'true' -or $env:SQLITE_CACHE_PILOT -ne '1' -or
        !$PinnedMsvcOnly -or $Platform.Count -ne 1 -or $Platform[0] -ne 'x64' -or
        $VerifyRestoredInvariantState -or $VerifySharedSqliteCompilation) {
        throw 'SQLite-only restoration is restricted to the isolated x64 pilot.'
    }
    $buildArgs += @('/t:Build', '/p:DoujinVerifiedSqlitePilot=true')
} elseif ($VerifyRestoredInvariantState) {
    if ($VerifySharedSqliteCompilation -or !$WindowsSdkVersion -or !$MsvcToolsVersion) {
        throw 'Restored-state experiment requires exact SDK/MSVC pins and cannot request a fresh SQLite compile.'
    }
    $buildArgs += '/t:Build'
} elseif ($WindowsSdkVersion -or $LLVMInstallDir) {
    $buildArgs += '/t:Rebuild'
}
$msbuild = Get-Command msbuild.exe -ErrorAction SilentlyContinue
if ($msbuild) {
    $exe = $msbuild.Source
} else {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (!(Test-Path $vswhere)) { throw 'Visual Studio Build Tools or Developer PowerShell is required.' }
    $exe = & $vswhere -latest -products '*' -requires Microsoft.Component.MSBuild -find 'MSBuild\Current\Bin\MSBuild.exe' | Select-Object -First 1
    if (!$exe) { throw 'MSBuild was not found.' }
}
$layout = @{ Win32 = ''; x64 = 'x64'; ARM64EC = 'arm64ec' }
$staging = Join-Path ([IO.Path]::GetTempPath()) ("foo_doujinstyle-" + [guid]::NewGuid())
New-Item -ItemType Directory $staging | Out-Null
try {
    Copy-Item (Join-Path $PSScriptRoot 'third_party\LICENSE.nlohmann-json') $staging
    Copy-Item (Join-Path $PSScriptRoot 'third_party\utf8proc\LICENSE.md') (Join-Path $staging 'LICENSE.utf8proc')
    $entries = New-Object 'System.Collections.Generic.List[string]'
    $entries.Add("LICENSE.nlohmann-json=" + (Join-Path $staging 'LICENSE.nlohmann-json'))
    $entries.Add("LICENSE.utf8proc=" + (Join-Path $staging 'LICENSE.utf8proc'))
    foreach ($arch in $Platform) {
        $archArgs = @($buildArgs)
        $useMsvc = (($arch -eq 'ARM64EC') -and $Arm64ecWithMsvc) -or
                   (($arch -eq 'Win32') -and $Win32WithMsvc) -or
                   (($arch -eq 'x64') -and $X64WithMsvc)
        if ($useMsvc) {
            # Visual Studio's MSBuild ClangCL toolset is not registered for ARM64EC
            # on the hosted Windows runner. This is an EXPLICIT, logged v143/MSVC
            # compiler choice only for the ARM64EC architecture.
            $archArgs = @($buildArgs | Where-Object {
                $_ -notmatch '^/p:(PlatformToolset|LLVMInstallDir|LLVMToolsVersion)='
            })
            $archArgs += '/p:PlatformToolset=v143'
            $reason = if ($arch -eq 'Win32') { 'LLVM23 Win32 speculative-load-hardening backend crash' } elseif ($arch -eq 'x64') { 'LLVM23 x64 speculative-load-hardening backend crash' } else { 'ARM64EC ClangCL unavailable' }
            Write-Host "Compiler target ${arch}: MSVC v143 explicit Spectre-mitigated exception ($reason)"
        } elseif ($PinnedMsvcOnly) {
            Write-Host "Compiler target ${arch}: explicitly pinned MSVC v143 $MsvcToolsVersion"
        } elseif ($LLVMInstallDir) {
            Write-Host "Compiler target ${arch}: ClangCL $actualVersion (verified)"
        } else {
            Write-Host "Compiler target ${arch}: project toolset (unqualified local build)"
        }
        Write-Host "BUILD-METRIC target=$arch logical_processors=$([Environment]::ProcessorCount) requested_compiler_jobs=$CompilerJobs"
        $clock = [Diagnostics.Stopwatch]::StartNew()
        $buildLog = Join-Path $staging "$arch-msbuild.log"
        & $exe (Join-Path $PSScriptRoot 'foo_doujinstyle.vcxproj') /m /v:minimal /clp:PerformanceSummary "/p:Platform=$arch" @archArgs |
            Tee-Object -FilePath $buildLog
        if ($LASTEXITCODE) { throw "Build failed for $arch." }
        $clock.Stop()
        $sqliteCompiles = @(Select-String -LiteralPath $buildLog -Pattern '^\s*(?:\d+>)?\s*sqlite_amalgamation[.]c\s*$').Count
        Write-Host "BUILD-METRIC target=$arch seconds=$([Math]::Round($clock.Elapsed.TotalSeconds,3)) sqlite_compiles=$sqliteCompiles"
        if ($VerifySharedSqliteCompilation -and $sqliteCompiles -ne 1) {
            throw "Fresh $arch build must compile SQLite once, observed $sqliteCompiles"
        }
        if ($VerifyRestoredSqliteOnly) {
            if ($sqliteCompiles -ne 0) { throw "Restored SQLite recompiled $sqliteCompiles times" }
            foreach ($source in @('component.cpp','descriptor.cpp','search.cpp','publish_references.cpp','worker_build_identity.cpp')) {
                if (!(Select-String -LiteralPath $buildLog -Pattern ('^\s*(?:\d+>)?\s*' + [regex]::Escape($source) + '\s*$') -Quiet)) {
                    throw "Missing fresh first-party compile: $source"
                }
            }
            Write-Host 'CACHE-METRIC sqlite_compiles=0 fresh_first_party=verified'
        }
        if ($VerifyRestoredInvariantState) {
            if ($sqliteCompiles -ne 0) { throw "Restored $arch compiled SQLite $sqliteCompiles times" }
            # The warm experiment deletes all first-party output/intermediate
            # directories. Any other compiler source diagnostic is unexpected.
            $allowed = @('component.cpp','descriptor.cpp','search.cpp','publish_references.cpp','worker_build_identity.cpp','utf8proc.c','host_trace_etw.cpp')
            $compiled = @(Get-Content -LiteralPath $buildLog | ForEach-Object {
                if ($_ -match '^\s*(?:\d+>)?\s*([^\s]+\.(?:cpp|c|cxx|cc))\s*$') {
                    [IO.Path]::GetFileName($Matches[1])
                }
            })
            foreach ($source in $compiled) {
                if ($source -notin $allowed) { throw "Unexpected invariant compile: $source" }
            }
            foreach ($source in @('component.cpp','descriptor.cpp','search.cpp','publish_references.cpp','worker_build_identity.cpp')) {
                if ($source -notin $compiled) { throw "Expected fresh first-party compilation missing: $source" }
            }
            Write-Host "CACHE-METRIC target=$arch invariant_compiles=0 first_party_compile_records=$($compiled.Count)"
        }
        $sqliteLib = Join-Path $PSScriptRoot "build\shared-lib\$arch\doujinstyle_sqlite.lib"
        if (!(Test-Path -LiteralPath $sqliteLib -PathType Leaf)) { throw "Missing shared SQLite library for $arch" }
        Write-Host "BUILD-METRIC target=$arch sqlite_lib_sha256=$((Get-FileHash -LiteralPath $sqliteLib -Algorithm SHA256).Hash.ToLowerInvariant())"
        $target = $staging
        if ($layout[$arch]) {
            $target = Join-Path $staging $layout[$arch]
            New-Item -ItemType Directory -Force $target | Out-Null
        }
        Copy-Item (Join-Path $PSScriptRoot "build\$arch\foo_doujinstyle.dll") $target
        $archiveName = if ($layout[$arch]) { "$($layout[$arch])/foo_doujinstyle.dll" } else { 'foo_doujinstyle.dll' }
        $entries.Add($archiveName + '=' + (Join-Path $target 'foo_doujinstyle.dll'))
    }
    $name = if ($Platform.Count -eq 3) {
        "foo_doujinstyle-$release.fb2k-component"
    } else {
        "foo_doujinstyle-$($Platform -join '-')-$release.fb2k-component"
    }
    if ($E2ETrace) {
        $name = $name -replace '[.]fb2k-component$', '-e2e-trace.fb2k-component'
    }
    $component = Join-Path (Join-Path $PSScriptRoot 'build') $name
    if (Test-Path $component) { Remove-Item $component }
    & py -3 (Join-Path $PSScriptRoot '..\tools\package_component.py') $component @entries
    if ($LASTEXITCODE) { throw 'Deterministic component packaging failed.' }
    Write-Output $component
} finally {
    Remove-Item -LiteralPath $staging -Recurse -Force
}
