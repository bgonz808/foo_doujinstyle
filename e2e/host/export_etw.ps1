# Convert only self-describing DoujinStyle test ETW events to the shared
# allowlisted JSONL schema. Requires a previously captured .etl file.
# This converter is intentionally strict; verify on a real guest before it
# becomes a host-quality gate.
param(
    [Parameter(Mandatory=$true)] [string]$EtlPath,
    [Parameter(Mandatory=$true)] [string]$OutputJsonl,
    [Parameter(Mandatory=$true)] [ValidatePattern('^[0-9a-f]{32}$')] [string]$ExpectedRunId
)
$ErrorActionPreference = 'Stop'
$provider = [Guid]'a4ab84a0-75db-4f17-b7e3-f1aa0d260d4a'
$allowed = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
foreach ($name in @(
    'component.track.open.begin', 'component.track.open.end',
    'component.archive.member.ready',
    'component.decoder.initialize.begin', 'component.decoder.initialize.end',
    'component.decoder.first_pcm', 'component.decoder.seek.begin',
    'component.decoder.seek.first_pcm',
    'component.ui.search.selection.changed',
    'component.ui.search.drag.request',
    'component.ui.search.drag.no_tracks',
    'component.ui.search.drag.has_tracks',
    'component.ui.search.drag.drop_accepted',
    'component.ui.search.open_release',
    'component.ui.search.track.updated',
    'component.ui.search.track.projected',
    'component.ui.search.worker.started',
    'component.ui.idle.worker.started',
    'component.ui.search.worker.exited'
)) { [void]$allowed.Add($name) }
if (!(Test-Path -LiteralPath $EtlPath -PathType Leaf)) { throw "Missing ETL file." }
if (Test-Path -LiteralPath $OutputJsonl) { throw "Refusing to overwrite prior trace evidence." }
$destination = [IO.Path]::GetFullPath($OutputJsonl)
$folder = [IO.Path]::GetDirectoryName($destination)
if (!(Test-Path -LiteralPath $folder -PathType Container)) { throw "Output folder absent." }

$stream = [IO.FileStream]::new($destination, [IO.FileMode]::CreateNew,
                              [IO.FileAccess]::Write, [IO.FileShare]::None)
$writer = [IO.StreamWriter]::new($stream, [Text.UTF8Encoding]::new($false))
$index = 0
try {
    foreach ($record in (Get-WinEvent -Path $EtlPath -Oldest -ErrorAction Stop)) {
        if ($record.ProviderId -ne $provider) { continue }
        [xml]$xml = $record.ToXml()
        $fields = @{}
        $nodes = $xml.SelectNodes("//*[local-name()='EventData']/*[local-name()='Data']")
        if (!$nodes -or $nodes.Count -eq 0) {
            throw "TraceLogging payload was not decoded into named EventData fields."
        }
        foreach ($node in $nodes) {
            $key = $node.GetAttribute('Name')
            if ($fields.ContainsKey($key)) { throw "Duplicate ETW payload field." }
            $fields[$key] = [string]$node.InnerText
        }
        foreach ($key in @('Event','RunId','AttemptId','ScenarioId',
                           'QpcTick','QpcFrequency','SampleCount','Pid','Tid')) {
            if (!$fields.ContainsKey($key)) { throw "ETW payload missing $key" }
        }
        if ($fields['RunId'] -cne $ExpectedRunId) { throw "ETW run ID mismatch." }
        if (!$allowed.Contains($fields['Event'])) { throw "Unexpected native trace event." }
        if ($fields['AttemptId'] -cnotmatch '^[A-Za-z0-9_-]{8,64}$' -or
            $fields['ScenarioId'] -cnotmatch '^[A-Za-z0-9_-]{8,64}$') {
            throw "Invalid ETW scenario or attempt ID."
        }
        $tick = [long]::Parse($fields['QpcTick'], [Globalization.CultureInfo]::InvariantCulture)
        $freq = [long]::Parse($fields['QpcFrequency'], [Globalization.CultureInfo]::InvariantCulture)
        $samples = [long]::Parse($fields['SampleCount'], [Globalization.CultureInfo]::InvariantCulture)
        $pid = [int]::Parse($fields['Pid'], [Globalization.CultureInfo]::InvariantCulture)
        if ($tick -lt 0 -or $freq -le 0 -or $samples -lt 0 -or $pid -lt 0) {
            throw "Invalid ETW numeric payload."
        }
        $entry = [ordered]@{
            schema_version = 1
            run_id = $ExpectedRunId
            scenario_id = $fields['ScenarioId']
            attempt_id = $fields['AttemptId']
            source = 'component'
            pid = $pid
            seq = $index
            event = $fields['Event']
            clock = @{
                domain = "win-qpc:$ExpectedRunId"
                tick = $tick
                frequency_hz = $freq
            }
            metrics = @{ sample_count = $samples }
        }
        $writer.WriteLine(($entry | ConvertTo-Json -Depth 6 -Compress))
        $index++
    }
} catch {
    $writer.Dispose()
    Remove-Item -LiteralPath $destination -Force -ErrorAction SilentlyContinue
    throw
} finally {
    $writer.Dispose()
}
if ($index -eq 0) {
    Remove-Item -LiteralPath $destination -Force
    throw "No qualifying native ETW events were captured."
}
Write-Output "Exported $index validated native milestones to $destination"
