#include "archive_digest.hpp"
#include <cassert>
#include <stdexcept>
int main() {
    doujin::archive::Sha256Stream h;
    h.update("a"); h.update("b"); h.update("c");
    assert(h.finish() == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    try { h.update("d"); assert(false); } catch (const std::runtime_error&) {}
    doujin::archive::Sha256Stream empty;
    assert(empty.finish() == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
}
