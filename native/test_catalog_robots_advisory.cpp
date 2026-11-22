#include "catalog_robots_advisory.hpp"
#include <cassert>
#include <cstdio>
using doujin::robots_advisory::inspect;
int main() {
    assert(!inspect("User-agent: *\nDisallow:\n").excluded);
    assert(!inspect("User-agent: *\nDisallow: /private\n").excluded);
    assert(inspect("User-agent: *\nDisallow: /\n").excluded);
    assert(inspect("User-agent: *\nDisallow: /\nAllow: /public\n", "/private").excluded);
    assert(!inspect("User-agent: *\nDisallow: /\nAllow: /public\n", "/public").excluded);
    assert(!inspect("User-agent: RandomBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n").excluded);
    assert(inspect("User-agent: *\nAllow: /\n\nUser-agent: DoujinStyle\nDisallow: /\n").excluded);
    assert(!inspect("User-agent: *\nDisallow: /\n\nUser-agent: DoujinStyle\nAllow: /\n").excluded);
    assert(inspect("User-agent: *\nDisallow: /\nAllow: /?p=home\n").excluded == false);
    assert(inspect("User-agent: *\nDisallow: /*\n").ambiguous);
    std::puts("native robots advisory: allow/disallow, groups, specificity, ambiguity: PASS");
}
