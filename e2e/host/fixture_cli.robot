*** Settings ***
Documentation     CLI-only host anchor; SDK config and common PCM evidence required.
Library           e2e.host.trace_keywords.TraceKeywords
Library           e2e.host.cli_host.CliHost
Suite Teardown    Run Keywords    Close Cli Host    AND    Close Trace

*** Test Cases ***
host.doujin.cli-first-common-pcm
    Run Marked Action    robot.host.launch.intent    Launch Cli Host
    Wait For Verified Cli Profile
    Add Cli Fixture
    Run Marked Action    robot.play.intent    Play Cli Fixture
    Wait For Cli Pcm
