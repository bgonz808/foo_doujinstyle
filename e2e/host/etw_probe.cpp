// Standalone test-only native provider exercise, never a foobar2000 test.
#include "../../native/host_trace_etw.hpp"
int main() {
    doujin::host_trace::emit("component.track.open.begin");
    doujin::host_trace::emit("component.decoder.initialize.begin");
    doujin::host_trace::emit("component.decoder.initialize.end");
    doujin::host_trace::emit("component.decoder.first_pcm", 480);
    doujin::host_trace::emit("component.track.open.end");
    return 0;
}
