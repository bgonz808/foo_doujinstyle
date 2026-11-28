param([Parameter(Mandatory=$true)][string]$Trace,
      [Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference='Stop'
# Raw ETL and XML are private, short-lived and never workflow artifacts.
$root = [IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd('\') + '\'
foreach ($path in @($Trace,$Output)) {
    if (![IO.Path]::GetFullPath($path).StartsWith($root,[StringComparison]::OrdinalIgnoreCase)) {
        throw 'Trace paths must remain in runner temp'
    }
}
$writer = [IO.StreamWriter]::new($Output,$false,[Text.UTF8Encoding]::new($false))
try {
    $writer.WriteLine('<Events>')
    Get-WinEvent -Path $Trace -Oldest | ForEach-Object {
        if ($_.ProviderId -eq [guid]'22fb2cd6-0e7b-422b-a0c7-2fad1fd0e716' -and $_.Id -in @(1,2,5)) {
            $writer.WriteLine($_.ToXml())
        }
    }
    $writer.WriteLine('</Events>')
} finally {
    $writer.Dispose()
}
