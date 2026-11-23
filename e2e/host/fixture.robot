*** Settings ***
Documentation     Real foobar2000, synthetic loopback ZIP, native first PCM required by runner.
...               Play uses foobar2000 command-line IPC, NOT a UIA Play click.
...               Null Output and correct PCM output are not yet verified.
Library           FlaUILibrary    uia=UIA3    timeout=15000
Library           e2e.host.trace_keywords.TraceKeywords
Library           e2e.host.fixture_commands.FixtureCommands
Suite Teardown    Run Keywords    Run Keyword And Ignore Error    Close Application    AND    Close Trace

*** Variables ***
${HOST_EXE}       ${EMPTY}

*** Test Cases ***
host.doujin.synthetic-zip-first-pcm
    [Documentation]    Real host startup, reference submission, intent and window.
    Should Not Be Empty    ${HOST_EXE}
    ${pid}=    Run Marked Action    robot.host.launch.intent    Launch Application    ${HOST_EXE}
    Should Be True    ${pid} > 0
    Wait Until Keyword Succeeds    20 s    500 ms    Element Should Be Visible    /Window
    Mark Trace Event    robot.host.window.visible
    Add Fixture To Playlist
    Run Marked Action    robot.play.intent    Play Fixture
    Sleep    4 s
    Take Screenshot
