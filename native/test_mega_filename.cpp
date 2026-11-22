#include "mega_filename.hpp"
#include <cassert>
#include <string>

int main() {
    using doujin::mega::archive_suffix;
    using doujin::mega::safe_filename;
    assert(archive_suffix("PK\x03\x04payload") == ".zip");
    assert(archive_suffix("7z\xbc\xaf\x27\x1c" "rest") == ".7z");
    assert(archive_suffix("Rar!\x1a\x07\x00") == ".rar");
    assert(archive_suffix("\x1f\x8b\x00") == ".gz");
    assert(safe_filename("Some Album.ZIP", "1", ".zip") == "Some Album.ZIP");
    assert(safe_filename("../../NUL", "42", ".zip") == "_.._NUL.zip");
    assert(safe_filename("CON.txt", "42", ".zip") == "DoujinStyle-42.zip");
    assert(safe_filename("Folder/Album:Tracks?.zip", "42", ".zip") == "Folder_Album_Tracks_.zip");
    assert(safe_filename("\xe5\xa4\x9c", "42", ".zip") == "\xe5\xa4\x9c.zip");
    const auto unicode_name = safe_filename(std::string(149, 'a') + "\xe5\xa4\x9c" +
                                            std::string(20, 'z') + ".zip", "42", ".zip");
    assert(unicode_name == std::string(149, 'a') + ".zip");
    const auto long_name = safe_filename(std::string(400, 'A') + ".zip", "42", ".zip");
    assert(long_name.size() == 154 && long_name.ends_with(".zip"));
}
