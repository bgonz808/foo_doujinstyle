param([switch]$ResolveDigest)
$ErrorActionPreference = 'Stop'
$version = '4.4.9.14'
$package = 'microsoft.codeanalysis.binskim'
$archive = Join-Path $env:RUNNER_TEMP "$package.$version.nupkg"
$url = "https://api.nuget.org/v3-flatcontainer/$package/$version/$package.$version.nupkg"
Invoke-WebRequest -Uri $url -OutFile $archive
$actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA512).Hash.ToLowerInvariant()
if ($ResolveDigest) {
    Write-Host "BinSkim $version NuGet SHA512 $actual"
    return
}
throw 'BinSkim scanner is not configured yet'
