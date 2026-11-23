*** Settings ***
Documentation     Phase 0 only: actual foobar2000 window, not playback or component qualification.
Library           FlaUILibrary    uia=UIA3    timeout=15000
Library           e2e.host.trace_keywords.TraceKeywords
Test Teardown     Run Keyword And Ignore Error    Take Screenshot
Suite Teardown    Run Keywords    Run Keyword And Ignore Error    Close Application    AND    Close Trace

*** Variables ***
${HOST_EXE}       ${EMPTY}

*** Test Cases ***
host.foobar.launch-window
    [Documentation]    Assert window and timestamp GUI launch intent, not simulated Play.
    Should Not Be Empty    ${HOST_EXE}
    ${pid}=    Run Marked Action    robot.host.launch.intent    Launch Application    ${HOST_EXE}
    Should Be True    ${pid} > 0
    Element Should Be Visible    /Window
    Mark Trace Event    robot.host.window.visible
    Take Screenshot
