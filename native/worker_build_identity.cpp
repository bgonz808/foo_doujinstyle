#include "worker_build_identity.hpp"
#include "doujinstyle_build_version.hpp"
#include <cstdio>

void doujin::print_worker_build_identity() {
    std::printf("%s\nsource=%s\ncommit=%s\n", DOUJIN_FULL_VERSION,
                DOUJIN_SOURCE_SHA256, DOUJIN_GIT_COMMIT);
}
