#pragma once

#include "site_discovery.hpp"

namespace doujin::discovery {
struct ReleaseDocument {
    unsigned status;
    std::string body, url, policy;
};

struct OfferResponse {
    unsigned status;
    std::string location;
};

inline std::string percent_encode(std::string_view value, std::string_view reserved = {}, bool form = false) {
    constexpr std::string_view hex = "0123456789ABCDEF";
    std::string result;
    for (const unsigned char ch : value) {
        if ((ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') ||
            std::string_view("-._~").find(ch) != reserved.npos || reserved.find(ch) != reserved.npos) {
            result += static_cast<char>(ch);
        } else if (form && ch == ' ') {
            result += '+';
        } else {
            result += '%';
            result += hex[ch >> 4];
            result += hex[ch & 15];
        }
    }
    return result;
}

inline std::string offer_location(const std::string& action, const OfferResponse& response) {
    if (response.status < 200 || response.status >= 400)
        throw std::runtime_error("DoujinStyle offer returned HTTP " + std::to_string(response.status));
    if (response.location.empty())
        throw std::runtime_error("The download form did not return a host redirect");
    auto target = resolve_url(action, response.location);
    if (lower_ascii(target.substr(0, 8)) != "https://")
        throw std::runtime_error("Unexpected download offer destination");
    target.replace(0, 5, "https");
    const auto end = target.find_first_of("/?#", 8);
    const auto authority = target.substr(0, end);
    validate_url(authority);
    const auto host = lower_ascii(target.substr(8, end == target.npos ? end : end - 8));
    const auto name = std::string_view(host).substr(0, host.find(':'));
    if (name == "doujinstyle.com" || name == "www.doujinstyle.com" ||
        name == "doujinstyle.com." || name == "www.doujinstyle.com.")
        throw std::runtime_error("Unexpected download offer destination");
    if (end == target.npos) return target;
    const auto path_end = target.find_first_of("?#", end);
    const auto path = std::string_view(target).substr(end, path_end - end);
    auto result = authority + percent_encode(path, "/%:@!$&'()*+,;=");
    const auto fragment = target.find('#', end);
    if (path_end != target.npos && target[path_end] == '?' && path_end + 1 != std::min(fragment, target.size()))
        result += '?' + percent_encode(std::string_view(target).substr(path_end + 1, fragment - path_end - 1),
                                       "/%:@!$&'()*+,;=?");
    if (fragment != target.npos && fragment + 1 != target.size())
        result += '#' + percent_encode(std::string_view(target).substr(fragment + 1), "/%:@!$&'()*+,;=?");
    if (result.size() > 8192) throw std::runtime_error("Download offer URL exceeds its byte limit");
    return result;
}

inline std::string offer_host(std::string_view url) {
    const auto tail = url.substr(8);
    const auto authority = tail.substr(0, tail.find_first_of("/?#"));
    if (authority.starts_with('[')) {
        const auto close = authority.find(']');
        if (close == authority.npos || close == 1) throw std::runtime_error("Invalid download offer host");
        return lower_ascii(std::string(authority.substr(1, close - 1)));
    }
    const auto host = authority.substr(0, authority.find(':'));
    if (host.empty()) throw std::runtime_error("Invalid download offer host");
    return lower_ascii(std::string(host));
}

inline std::string decode_offer_filename(std::string_view segment) {
    std::string result;
    for (std::size_t i = 0; i < segment.size(); ++i) {
        if (segment[i] == '%' && segment.size() - i >= 3) {
            unsigned value = 0;
            const auto* first = segment.data() + i + 1;
            const auto hex = std::from_chars(first, first + 2, value, 16);
            if (hex.ec == std::errc{} && hex.ptr == first + 2) {
                result += static_cast<char>(value);
                i += 2;
                continue;
            }
        }
        result += segment[i];
    }
    return result;
}

inline Json describe_offer(std::string_view url) {
    Json offer{{"host", offer_host(url)}, {"filename", nullptr}, {"format_hint", nullptr}};
    const auto first = url.find_first_of("/?#", 8);
    if (first == url.npos || url[first] != '/') return offer;
    auto path = url.substr(first, url.find_first_of("?#", first) - first);
    while (!path.empty()) {
        const auto slash = path.rfind('/');
        auto name = decode_offer_filename(path.substr(slash == path.npos ? 0 : slash + 1));
        std::string_view leaf(name);
        while (!leaf.empty()) {
            const auto last = leaf.find_last_not_of('/');
            if (last == leaf.npos) break;
            leaf = leaf.substr(0, last + 1);
            const auto split = leaf.rfind('/');
            const auto component = leaf.substr(split == leaf.npos ? 0 : split + 1);
            if (component != ".") {
                const auto dot = component.rfind('.');
                if (dot != component.npos && dot > 0) {
                    const auto extension = lower_ascii(std::string(component.substr(dot + 1)));
                    if (html_token("zip rar 7z gz", extension)) {
                        // Python unquote replaces invalid UTF-8 in path segments.
                        offer["filename"] = Json::parse(Json(name).dump(-1, ' ', false, Json::error_handler_t::replace));
                        offer["format_hint"] = extension;
                        return offer;
                    }
                }
                break;
            }
            leaf = split == leaf.npos ? std::string_view{} : leaf.substr(0, split);
        }
        if (slash == path.npos) break;
        path = path.substr(0, slash);
    }
    return offer;
}

inline bool inspectable_offer(std::string_view url, const Json& offer) {
    const std::string_view host(offer.at("host").get_ref<const std::string&>());
    const auto last = host.find_last_not_of('.');
    const auto family_host = last == host.npos ? std::string_view{} : host.substr(0, last + 1);
    const auto is_family = [family_host](std::string_view domain) {
        return family_host == domain || (family_host.ends_with(domain) && family_host.size() > domain.size() &&
            family_host[family_host.size() - domain.size() - 1] == '.');
    };
    if (is_family("mediafire.com")) return host == family_host;
    if (is_family("mega.nz") || is_family("mega.co.nz")) {
        try {
            parse_mega_link(url);
            return true;
        } catch (const std::runtime_error&) {
            return false;
        }
    }
    return !is_family("drive.google.com") && !is_family("drive.usercontent.google.com") &&
        !offer.at("format_hint").is_null();
}

class ReleaseOffers {
public:
    ReleaseOffers(std::int64_t id, const ReleaseDocument& document)
        : page(detail(document.body, id, document.url, document.policy)), id_(id) {}

    template<typename Allowed, typename Post>
    std::string submit(std::int64_t id, std::string_view slot, Allowed allowed, Post post) {
        const auto& forms = page.at("forms");
        if (id != id_ || next_ == forms.size() || forms[next_].value("source", "0") != slot)
            throw std::runtime_error("No pending DoujinStyle download form for this release and slot");
        // Consume before sending. A timeout after submission must never replay this form.
        const Json& form = forms[next_++];
        const auto& action = form.at("action").get_ref<const std::string&>();
        const auto& source = form.at("document_url").get_ref<const std::string&>();
        const auto& policy = form.at("referrer_policy").get_ref<const std::string&>();
        if (!allowed(action) || form.at("method") != "post" ||
            navigation_referrer(action, action, "origin") != navigation_referrer(source, source, "origin"))
            throw std::runtime_error("Unexpected DoujinStyle download form destination or method");
        std::string content;
        for (const char* field : {"id", "type", "source"}) {
            if (!form.contains(field)) continue;
            if (!content.empty()) content += '&';
            content += field;
            content += '=';
            content += percent_encode(form.at(field).get_ref<const std::string&>(), {}, true);
        }
        content += "&download_link=" + percent_encode(form.at("button_value").get_ref<const std::string&>(), {}, true);
        return offer_location(action, post(action, source, policy, content));
    }

    template<typename Allowed, typename Post>
    Json resolve(bool all_forms, Allowed allowed, Post post) {
        const auto& forms = page.at("forms");
        auto observation = page.at("row");
        observation["download_forms"] = forms;
        auto offers = Json::array();
        Json error;
        if (forms.empty()) error = "No download offer was found";
        for (std::size_t index = 0; index < forms.size(); ++index) {
            const auto slot = forms[index].value("source", "0");
            const auto number = integer(slot).value();
            const auto source = number == 0 ? "doujinstyle" : "doujinstyle:" + std::to_string(number);
            try {
                auto url = submit(id_, slot, allowed, post);
                auto offer = describe_offer(url);
                const bool stop = !all_forms && index == 0 && inspectable_offer(url, offer);
                offers.push_back(Json::array({source, std::move(offer), std::move(url)}));
                if (stop) break;
            } catch (const std::runtime_error& failure) {
                if (error.is_null()) error = failure.what();
            }
        }
        return {{"observation", std::move(observation)}, {"offers", std::move(offers)}, {"error", std::move(error)}};
    }

    const Json page;

private:
    std::int64_t id_;
    std::size_t next_ = 0;
};
}
