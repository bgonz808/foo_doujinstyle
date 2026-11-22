#include "site_offers.hpp"
#ifdef _WIN32
#include "mediafire_http.hpp"
#endif

#include <cassert>
#include <iostream>

using doujin::discovery::OfferResponse;
using doujin::discovery::ReleaseOffers;

int main(int argc, char** argv) {
#ifdef _WIN32
    if (argc == 2) {
        const std::string url = argv[1];
        const auto origin = url.substr(0, url.find('/', 7));
        if (!origin.starts_with("http://127.0.0.1:")) return 2;
        const auto loopback = [&](std::string_view target) { return target.starts_with(origin + '/'); };
        doujin::MediaFireHttp session;
        const auto document = session.catalog_document(url, doujin::catalog_html_limit, loopback);
        if (document.status != 200) return 3;
        ReleaseOffers release(25, document);
        const auto result = release.resolve(false, loopback, [&](const auto& action, const auto& source,
                                                               const auto& policy, const auto& body) {
            return session.catalog_post(action, source, policy, body);
        });
        std::cout << result.dump(-1, ' ', true) << '\n';
        return 0;
    }
#else
    (void)argc;
    (void)argv;
#endif
    // Native idle pagination agrees with the Python source implementation and
    // will not follow an off-origin or unobserved navigation destination.
    const std::string browse =
        R"(<meta name="referrer" content="origin"><div class="pageScroll">)"
        R"(<a href="?p=home&amp;page=7">7</a>)"
        R"(<a href="https://evil.invalid/?p=home&amp;page=9">evil</a>)"
        R"(<a href="?p=home&amp;page=6" rel="noreferrer">6</a>)"
        R"(<a href="?p=home&amp;page=5" referrerpolicy="same-origin">5</a>)"
        R"(<a href="?p=search&amp;page=4">search</a></div>)"
        R"(<a href="?p=home&amp;page=3">outside navigation</a>)";
    const auto links = doujin::discovery::browse_links(browse,
        "https://doujinstyle.com/?p=home&page=8", "strict-origin");
    assert(links.size() == 4);
    assert(links.at("8").at(1) == "origin");
    assert(links.at("7").at(0) == "https://doujinstyle.com/?p=home&page=7");
    assert(links.at("6").at(1) == "no-referrer");
    assert(links.at("5").at(1) == "same-origin");
    assert(!links.contains("9") && !links.contains("3") && !links.contains("4"));
    bool browse_bad_url = false;
    try {
        (void)doujin::discovery::browse_links(browse, "https://evil.invalid/?p=home&page=8", "");
    } catch (const std::runtime_error&) { browse_bad_url = true; }
    assert(browse_bad_url);

    const std::string page =
        R"(<mainbar><h2>Release</h2><form method="get" action="/ignored">)"
        R"(<input type="hidden" name="id" value="25"><input type="hidden" name="type" value="1">)"
        R"(<input type="hidden" name="source" value="3">)"
        R"(<button name="download_link" formaction="/selected" formmethod="post" value="夜 +&amp;">Link</button>)"
        R"(</form></mainbar>)";
    const std::string document = "https://doujinstyle.com/page?id=25";
    ReleaseOffers release(25, {200, page, document, "origin"});
    const auto allowed = doujin::discovery::site_url;
    unsigned posts = 0;
    const auto submit = [&](const auto& action, const auto& source, const auto& policy, const auto& body) {
        ++posts;
        assert(action == "https://doujinstyle.com/selected");
        assert(source == document && policy == "origin");
        assert(body == "id=25&type=1&source=3&download_link=%E5%A4%9C+%2B%26");
        return OfferResponse{302, "//www.mediafire.com/file/abc/夜 Mix.zip?x=%2B&name=曲"};
    };
    const auto rejected = [](auto action) {
        try {
            action();
            return false;
        } catch (const std::runtime_error&) {
            return true;
        }
    };
    assert(rejected([&] { release.submit(26, "3", allowed, submit); }));
    assert(rejected([&] { release.submit(25, "4", allowed, submit); }));
    assert(posts == 0);
    const auto target = release.submit(25, "3", allowed, submit);
    assert(target == "https://www.mediafire.com/file/abc/%E5%A4%9C%20Mix.zip?x=%2B&name=%E6%9B%B2");
    assert(rejected([&] { release.submit(25, "3", allowed, submit); }));
    assert(posts == 1);

    const auto normalize = [&](unsigned status, const std::string& location) {
        return doujin::discovery::offer_location(document, {status, location});
    };
    assert(normalize(307, "https://mega.nz/file/abcdefgh#some-key") ==
           "https://mega.nz/file/abcdefgh#some-key");
    assert(rejected([&] { normalize(403, "https://example.test/offer"); }));
    assert(rejected([&] { normalize(302, ""); }));
    assert(rejected([&] { normalize(302, "../tracking"); }));
    assert(rejected([&] { normalize(302, "https://user@other.test/"); }));
    assert(rejected([&] { normalize(302, "https://doujinstyle.com./other"); }));
    ReleaseOffers failed(25, {200, page, document, ""});
    assert(rejected([&] {
        failed.submit(25, "3", allowed, [](const auto&...) -> OfferResponse {
            throw std::runtime_error("simulated transport failure");
        });
    }));
    assert(rejected([&] { failed.submit(25, "3", allowed, submit); }));
    assert(posts == 1);
}
