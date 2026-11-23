*** Settings ***
Documentation     Phase 0: real foobar2000 window only, not component qualification.
Library           FlaUILibrary    uia=UIA3    timeout=15000
Suite Teardown    Run Keyword And Ignore Error    Close Application

*** Variables ***
${HOST_EXE}       ${EMPTY}

*** Test Cases ***
host.foobar.launch-window
    [Documentation]    Verify a real foobar2000 window in an isolated guest.
    Should Not Be Empty    ${HOST_EXE}
    ${pid}=    Launch Application    ${HOST_EXE}
    Should Be True    ${pid} > 0
    Element Should Be Visible    /Window
    Take Screenshot
