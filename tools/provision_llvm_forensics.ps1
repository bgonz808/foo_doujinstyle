$ErrorActionPreference = 'Stop'
# Same official archive identity as Windows analysis; optional research closure.
$version = '23.1.6'
$asset = "clang+llvm-$version-x86_64-pc-windows-msvc.tar.zst"
$archive = Join-Path $env:RUNNER_TEMP $asset
$expected = '7623f582089983c1cb51030cf1996514b19464e5669099571b399e8543770155'
Invoke-WebRequest -Uri "https://github.com/llvm/llvm-project/releases/download/llvmorg-$version/$asset" -OutFile $archive
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) { throw 'LLVM archive digest mismatch' }
$stage = Join-Path $env:RUNNER_TEMP 'llvm-forensics'
New-Item -ItemType Directory -Path $stage | Out-Null
$decoder = (Get-Command 7z.exe -CommandType Application -ErrorAction Stop).Source
py -3 -m tools.llvm_archive_selection --archive $archive --decoder $decoder --destination $stage --forensics
if ($LASTEXITCODE) { throw 'LLVM forensic closure extraction failed' }
$found = @(Get-ChildItem -LiteralPath $stage -Recurse -File -Filter clang-cl.exe)
if ($found.Count -ne 1) { throw 'Ambiguous LLVM compiler' }
$bin = $found[0].DirectoryName
if ((Get-FileHash -LiteralPath $found[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant() -ne '0d1fb4523a41c8ec722cdb72909a0a8fff1343700125b3f11d5cf83063a4d7e3') { throw 'LLVM compiler identity mismatch' }
# All added executables originate from the same verified archive and validated
# extraction. Record individual digests before any execution; no auto-approval.
foreach ($name in @('clang-cl.exe','lld-link.exe','llvm-readobj.exe','llvm-pdbutil.exe','llvm-objdump.exe','llvm-lib.exe','llvm-ar.exe')) {
    $path = Join-Path $bin $name
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Missing forensic tool' }
    Write-Host "FORENSIC-TOOL $name sha256=$((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant())"
}
$banner = (& $found[0].FullName --version) -join "\n"
if ($LASTEXITCODE -or $banner -notmatch 'clang version 23\.1\.6\b') { throw 'Unexpected LLVM version' }
"LLVM_FORENSIC_BIN=$bin" >> $env:GITHUB_ENV
