param([Parameter(Mandatory)][string]$SdkRoot)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $SdkRoot).Path
# The SDK archive is SHA-256 verified before this script is invoked.
$projects = @(
    'foobar2000\SDK\foobar2000_SDK.vcxproj',
    'foobar2000\foobar2000_component_client\foobar2000_component_client.vcxproj',
    'pfc\pfc.vcxproj'
)
foreach ($relative in $projects) {
    $path = Join-Path $root $relative
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing expected SDK project: $relative" }
    [xml]$project = Get-Content -Raw -LiteralPath $path
    $top = $project.DocumentElement
    if ($top.LocalName -ne 'Project') { throw "Unexpected SDK project structure: $relative" }
    $ns = $top.NamespaceURI
    $group = $project.CreateElement('ItemDefinitionGroup', $ns)
    $compile = $project.CreateElement('ClCompile', $ns)
    foreach ($entry in @(
        @{ Name = 'BufferSecurityCheck'; Value = 'true' },
        @{ Name = 'AdditionalOptions'; Value = '/ZH:SHA_256 %(AdditionalOptions)' }
    )) {
        $node = $project.CreateElement($entry.Name, $ns)
        $node.InnerText = $entry.Value
        [void]$compile.AppendChild($node)
    }
    # MSVC ARM64EC must emit /Qspectre and link mitigated runtimes.
    # clang-cl's /Qspectre is ignored; compile with LLVM SLH instead.
    $spectre = $project.CreateElement('SpectreMitigation', $ns)
    $spectre.SetAttribute('Condition', "'`$(PlatformToolset)' != 'ClangCL'")
    $spectre.InnerText = 'Spectre'
    [void]$compile.AppendChild($spectre)
    $slh = $project.CreateElement('AdditionalOptions', $ns)
    $slh.SetAttribute('Condition', "'`$(PlatformToolset)' == 'ClangCL'")
    $slh.InnerText = '-mspeculative-load-hardening %(AdditionalOptions)'
    [void]$compile.AppendChild($slh)
    [void]$group.AppendChild($compile)
    [void]$top.AppendChild($group)
    $project.Save($path)
}
Write-Host 'SDK project compiler mitigations configured'
