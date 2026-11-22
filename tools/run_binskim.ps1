param([switch]$ResolveDigest)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$version = '4.4.9.14'
$package = 'microsoft.codeanalysis.binskim'
# Pinned SHA-512 of the NuGet archive; do not execute unverified tools.
$expectedSha512 = 'f627d17dfa7927a8352a2cf329c370a74aecfbd759141d89d0d0b20541ff220ae903951aab9edcd444a24980cfae20c939d7b9c69d380482a439341aa35a7838'
$archive = Join-Path $env:RUNNER_TEMP "$package.$version.nupkg"
$url = "https://api.nuget.org/v3-flatcontainer/$package/$version/$package.$version.nupkg"
Invoke-WebRequest -Uri $url -OutFile $archive
$actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA512).Hash.ToLowerInvariant()
if ($ResolveDigest) {
    Write-Host "BinSkim $version NuGet SHA512 $actual"
    return
}
if ($actual -ne $expectedSha512) { throw "BinSkim package SHA-512 mismatch ($version)" }
$stage = Join-Path $env:RUNNER_TEMP 'binskim-pinned'
New-Item -ItemType Directory -Force -Path $stage | Out-Null
[System.IO.Compression.ZipFile]::ExtractToDirectory($archive, $stage)
$tools = @(Get-ChildItem -LiteralPath $stage -Recurse -File -Filter 'BinSkim.exe' |
    Where-Object { $_.FullName -match '[\\/]win-x64[\\/]BinSkim[.]exe$' })
if ($tools.Count -ne 1) { throw "Expected one Windows x64 BinSkim CLI; found $($tools.Count)" }
$exe = $tools[0].FullName
$banner = (& $exe --version 2>&1) -join ' '
if ($LASTEXITCODE -ne 0 -or $banner -notmatch [regex]::Escape($version)) {
    throw "Unexpected BinSkim CLI version: $banner"
}
$build = Join-Path $PSScriptRoot '..\native\build'
$reports = Join-Path $env:RUNNER_TEMP 'binskim-sarif'
New-Item -ItemType Directory -Force -Path $reports | Out-Null
foreach ($arch in @('Win32', 'x64', 'ARM64EC')) {
    foreach ($name in @('foo_doujinstyle.dll', 'publish_references.exe')) {
        $file = Join-Path $build "$arch\$name"
        if (!(Test-Path -LiteralPath $file -PathType Leaf)) {
            throw "Missing BinSkim target: $arch/$name"
        }
        $report = Join-Path $reports "$arch-$name.sarif"
        $folder = Join-Path $build $arch
        $log = Join-Path $reports "$arch-$name.log"
        & $exe analyze $file --output $report --disable-archive-extraction --kind 'Fail;Pass;Review;Open;NotApplicable;Informational' --local-symbol-directories $folder *> $log
        $code = $LASTEXITCODE
        if ($code -ne 0) {
            Get-Content -LiteralPath $log -Tail 15 | ForEach-Object { Write-Host $_ }
            throw "BinSkim analysis failed for $arch/$name (exit $code)"
        }
        if (!(Test-Path -LiteralPath $report)) { throw "Missing BinSkim report: $arch/$name" }
    }
}
& py -3 (Join-Path $PSScriptRoot 'binskim_policy.py') --build $build --reports $reports --baseline (Join-Path $PSScriptRoot 'binskim-baseline.json') --policy (Join-Path $PSScriptRoot '..\security\bin-policy.json')
if ($LASTEXITCODE -ne 0) { throw 'BinSkim SARIF gate failed' }
