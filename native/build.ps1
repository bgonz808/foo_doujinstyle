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
    [switch]$E2ETrace
)
$ErrorActionPreference = 'Stop'
$baseVersion = (Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot '..\VERSION')).Trim()
if ($baseVersion -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') { throw 'Invalid VERSION.' }
$identityJson = & py -3 (Join-Path $PSScriptRoot '..\tools\build_identity.py')
if ($LASTEXITCODE) { throw 'Could not determine build identity.' }
$identity = $identityJson | ConvertFrom-Json
$release = "v$($identity.version)"
$sdk = (Resolve-Path $SdkRoot).Path
$buildArgs = @('/p:Configuration=Release', "/p:FoobarSdkRoot=$sdk")
if ($E2ETrace) {
    $buildArgs += '/p:DoujinE2ETrace=true'
    Write-Warning 'TEST-ONLY ETW instrumented component. Not a release artifact.'
}
if ($WindowsSdkVersion) {
    $buildArgs += "/p:WindowsTargetPlatformVersion=$WindowsSdkVersion"
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
if ($WindowsSdkVersion -or $LLVMInstallDir) {
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
        if ($arch -eq 'ARM64EC' -and $Arm64ecWithMsvc) {
            # Visual Studio's MSBuild ClangCL toolset is not registered for ARM64EC
            # on the hosted Windows runner. This is an EXPLICIT, logged v143/MSVC
            # compiler choice only for the ARM64EC architecture.
            $archArgs = @($buildArgs | Where-Object {
                $_ -notmatch '^/p:(PlatformToolset|LLVMInstallDir|LLVMToolsVersion)='
            })
            $archArgs += '/p:PlatformToolset=v143'
            Write-Host 'Compiler target ARM64EC: MSVC v143 (explicit exception; LLVM23 ClangCL for Win32/x64)'
        } elseif ($LLVMInstallDir) {
            Write-Host "Compiler target ${arch}: ClangCL $actualVersion (verified)"
        } else {
            Write-Host "Compiler target ${arch}: project toolset (unqualified local build)"
        }
        & $exe (Join-Path $PSScriptRoot 'foo_doujinstyle.vcxproj') /m /v:minimal "/p:Platform=$arch" @archArgs
        if ($LASTEXITCODE) { throw "Build failed for $arch." }
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
    if (Test-Path -LiteralPath $component) { Remove-Item -LiteralPath $component }
    & py -3 (Join-Path $PSScriptRoot '..\tools\package_component.py') $component @entries
    if ($LASTEXITCODE) { throw 'Deterministic component packaging failed.' }
    Write-Output $component
} finally {
    Remove-Item -LiteralPath $staging -Recurse -Force
}
