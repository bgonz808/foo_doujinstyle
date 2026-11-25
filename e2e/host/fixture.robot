*** Settings ***
Documentation     Real foobar2000, synthetic loopback ZIP, native first PCM required by runner.
...               Play uses foobar2000 command-line IPC, NOT a UIA Play click.
...               Null Output and correct PCM output are not yet verified.
Library           FlaUILibrary    uia=UIA3    timeout=15000
Library           e2e.host.trace_keywords.TraceKeywords
Library           e2e.host.ui_discovery.UiDiscovery
Library           e2e.host.fixture_commands.FixtureCommands
Test Teardown     Run Keyword And Ignore Error    Take Screenshot    /Window
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
    Capture UI State    main-window    ${pid}
    Add Fixture To Playlist
    Capture UI State    playlist-added    ${pid}
    Run Marked Action    robot.play.intent    Play Fixture
    Mark Trace Event    robot.play.dispatch.return
    Sleep    4 s
    Capture UI State    post-play-dispatch    ${pid}
