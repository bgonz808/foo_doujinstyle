# Read-only, process-scoped Windows UI Automation control-view inspection.
param(
    [Parameter(Mandatory=$true)][ValidateRange(1,2147483647)][int]$TargetPid,
    [Parameter(Mandatory=$true)][string]$ExpectedExe,
    [ValidateRange(1,24)][int]$MaxDepth=16,
    [ValidateRange(1,4096)][int]$MaxNodes=2048
)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes,WindowsBase
$process=Get-Process -Id $TargetPid -ErrorAction Stop
if (!$process.Path -or ![string]::Equals([IO.Path]::GetFullPath($process.Path),[IO.Path]::GetFullPath($ExpectedExe),[StringComparison]::OrdinalIgnoreCase)) {
    throw 'Target PID executable mismatch'
}
$started=$process.StartTime.ToUniversalTime().Ticks
$walker=[System.Windows.Automation.TreeWalker]::ControlViewWalker
$root=[System.Windows.Automation.AutomationElement]::RootElement
$condition=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ProcessIdProperty,$TargetPid)
$windows=$root.FindAll([System.Windows.Automation.TreeScope]::Children,$condition)
$nodes=[System.Collections.Generic.List[object]]::new()
$script:truncated=$false
$script:foreign=0
$deadline=[Diagnostics.Stopwatch]::StartNew()
function Bounded-Text([string]$Value) {
    if ($Value.Length -gt 1024) { $script:truncated=$true; return $Value.Substring(0,1024) }
    return $Value
}
function Visit-Element($Element,[int]$Parent,[int]$Depth) {
    if ($nodes.Count -ge $MaxNodes -or $Depth -gt $MaxDepth -or $deadline.Elapsed.TotalSeconds -gt 20) {
        $script:truncated=$true
        return
    }
    $current=$Element.Current
    if ($current.ProcessId -ne $TargetPid) { $script:foreign++; return }
    $index=$nodes.Count
    $rect=$current.BoundingRectangle
    $bounds=$null
    if (!$rect.IsEmpty -and ![double]::IsInfinity($rect.X) -and ![double]::IsInfinity($rect.Y) -and ![double]::IsInfinity($rect.Width) -and ![double]::IsInfinity($rect.Height)) {
        $bounds=@($rect.X,$rect.Y,$rect.Width,$rect.Height)
    }
    $name=''
    if (!$current.IsPassword) { $name=Bounded-Text $current.Name }
    $patterns=@($Element.GetSupportedPatterns() | ForEach-Object { $_.ProgrammaticName } | Sort-Object -Unique)
    $nodes.Add([ordered]@{
        index=$index; parent=$Parent; depth=$Depth; pid=$TargetPid
        name=$name; automation_id=(Bounded-Text $current.AutomationId)
        control_type=$current.ControlType.ProgrammaticName
        class_name=(Bounded-Text $current.ClassName)
        framework_id=(Bounded-Text $current.FrameworkId)
        enabled=[bool]$current.IsEnabled; offscreen=[bool]$current.IsOffscreen
        password=[bool]$current.IsPassword; keyboard_focusable=[bool]$current.IsKeyboardFocusable
        bounds=$bounds; patterns=$patterns
    })
    $child=$walker.GetFirstChild($Element)
    while ($null -ne $child) {
        if ($nodes.Count -ge $MaxNodes -or $Depth -ge $MaxDepth -or $deadline.Elapsed.TotalSeconds -gt 20) {
            $script:truncated=$true; break
        }
        Visit-Element $child $index ($Depth+1)
        $child=$walker.GetNextSibling($child)
    }
}
foreach ($window in $windows) {
    if ($nodes.Count -ge $MaxNodes -or $deadline.Elapsed.TotalSeconds -gt 20) { $script:truncated=$true; break }
    Visit-Element $window -1 0
}
if ($nodes.Count -eq 0) { throw 'No process-scoped UIA elements found' }
$again=Get-Process -Id $TargetPid -ErrorAction Stop
if ($again.StartTime.ToUniversalTime().Ticks -ne $started) { throw 'Target process changed' }
[ordered]@{schema_version=1; kind='uia-control-view'; pid=$TargetPid;
    truncated=$script:truncated; excluded_foreign_subtrees=$script:foreign;
    nodes=@($nodes.ToArray())} | ConvertTo-Json -Depth 8 -Compress
