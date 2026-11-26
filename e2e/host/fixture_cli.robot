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
    Mark Trace Event    robot.play.dispatch.return
    Wait For Cli Pcm
    ${seek_enabled}=    Seek Oracle Enabled
    IF    ${seek_enabled}
        FOR    ${ordinal}    IN RANGE    1    5
            Run Marked Action    robot.seek.intent    Seek Cli Fixture    ${ordinal}
            Wait For Seek Pcm    ${ordinal}
        END
    END
