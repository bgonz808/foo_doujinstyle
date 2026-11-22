#include "zip_name_resolution.hpp"
#include <cassert>
#include <string>
#include <vector>

using doujin::zip_name::Evidence;
using doujin::zip_name::resolve;

int main() {
    {
        const std::vector<Evidence> records{{"cover.jpg", 0, "", false}, {"\xc3\xa9.mp3", 0x800, "", false}};
        const auto names = resolve(records);
        assert(names.size() == 2 && names[0].name == "cover.jpg" && names[0].encoding == "cp437");
        assert(names[1].name == "\xc3\xa9.mp3" && names[1].encoding == "utf-8");
    }
    {
        const std::vector<Evidence> records{{"\xff.mp3", 0x800, "\xe6\x97\xa5.mp3", false}};
        const auto names = resolve(records);
        assert(names.size() == 1 && names[0].name == "\xe6\x97\xa5.mp3");
        assert(names[0].encoding == "unicode-path" && names[0].warning);
    }
    {
        // Valid CP932 Japanese name makes that encoding available to other members
        // with weaker individual evidence, but never overrides a UTF-8 flag.
        const std::vector<Evidence> records{{"\x82\xa0\x82\xa2\x82\xa4.mp3", 0, "", false},
                                            {"\x82\xa0.mp3", 0, "", false},
                                            {"\xc3\xa9.mp3", 0x800, "", false}};
        const auto names = resolve(records);
        assert(names.size() == 3 && names[0].encoding == "cp932-inferred");
        assert(names[1].encoding == "cp932-inferred" && names[2].encoding == "utf-8");
    }
    {
        const std::vector<Evidence> records{{"\x82\xa0.mp3", 0, "", false}};
        assert(resolve(records)[0].encoding == "cp437");
    }
    {
        const std::vector<Evidence> records{{"hello.mp3", 0, "", true}};
        assert(resolve(records)[0].warning);
    }
}
