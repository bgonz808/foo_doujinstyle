#pragma once

#include "deflate_stream.hpp"
#include "zip_catalog.hpp"
#include <algorithm>
#include <functional>
#include <map>
#include <memory>

namespace doujin {
struct CatalogRangeReply {
    ContentRange range;
    std::string body, etag, modified, disposition;
};

using CatalogFetch = std::function<CatalogRangeReply(std::optional<std::uint64_t>, std::size_t,
                                                      const std::string&)>;
class CatalogRangeChanged : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

class CatalogZipRange {
    using Spans = std::map<std::uint64_t, std::shared_ptr<const std::string>>;
public:
    CatalogZipRange(CatalogFetch fetch, const DeflateApi& zlib, std::size_t bytes, unsigned requests)
        : fetch_(std::move(fetch)), zlib_(zlib), budget_(bytes), max_requests_(requests) {}

    bool open(std::size_t suffix) {
        const auto bytes = std::min(suffix, budget_);
        const auto& tail = fetch({}, bytes);
        auto layout = zip_catalog_layout(size, tail);
        if (!layout && tail.size() < std::min(size, zip_catalog_max_tail) && budget_ > fetched) {
            const auto start = size - std::min(size, zip_catalog_max_tail);
            if (size - start - tail.size() <= budget_ - fetched)
                layout = zip_catalog_layout(size, read(start, static_cast<std::size_t>(size - start)));
        }
        if (!layout) return false;
        auto members = zip_catalog_members(read(layout->start, static_cast<std::size_t>(layout->bytes)),
                                          layout->start, layout->entries);
        if (!members) return false;
        for (std::size_t index = 0; index < members->size(); ++index) {
            if (!ordered_.try_emplace((*members)[index].header, index).second) return false;
        }
        rows = std::move(*members);
        directory_ = layout->start;
        directory_spans_ = spans_;
        return true;
    }

    std::string signature(std::size_t limit = 8) {
        if (!size) throw std::runtime_error("Archive header unavailable");
        return read(0, static_cast<std::size_t>(std::min<std::uint64_t>(size, limit)));
    }

    void begin_probe(std::size_t bytes, unsigned limit) {
        spans_ = directory_spans_;
        budget_ = fetched + bytes;
        max_requests_ = requests + limit;
    }

    std::string member(std::uint64_t header, std::size_t limit) {
        const auto at = ordered_.find(header);
        if (at == ordered_.end()) throw std::runtime_error("Unknown ZIP member offset");
        const auto& row = rows[at->second];
        const auto next = std::next(at);
        const auto boundary = next == ordered_.end() ? directory_ : next->first;
        const auto payload = zip_catalog_payload(read(header, 30 + row.name.size()), row.name, header, boundary,
                                                 row.packed, row.size, row.crc, row.method, row.flags);
        if (!payload) throw std::runtime_error("ZIP local header failed native validation");
        const auto want = static_cast<std::size_t>(std::min<std::uint64_t>(row.size, limit));
        const bool complete = want == row.size;
        if (row.method == 0) {
            auto data = read(*payload, want);
            if (complete && zlib_.checksum(0, reinterpret_cast<const unsigned char*>(data.data()),
                                           static_cast<uInt>(data.size())) != row.crc)
                throw std::runtime_error("ZIP member length or CRC mismatch");
            return data;
        }
        if (row.packed > UINT32_MAX || row.size > UINT32_MAX)
            throw std::runtime_error("Native prefix inflater cannot represent this ZIP member size");
        ZipMember entry{*payload, static_cast<std::uint32_t>(row.packed), static_cast<std::uint32_t>(row.size),
                        row.crc, row.method};
        DeflateStream stream(zlib_, entry);
        std::string output(want + (complete ? 1 : 0), '\0');
        std::size_t produced = 0;
        std::uint64_t consumed = 0;
        std::string input;
        std::size_t used = 0;
        while (!stream.finished() && (complete || produced < want)) {
            if (used == input.size() && consumed < row.packed) {
                input = read(*payload + consumed, static_cast<std::size_t>(std::min<std::uint64_t>(4096, row.packed - consumed)));
                used = 0;
            }
            const auto segment = std::span(reinterpret_cast<const unsigned char*>(input.data()), input.size()).subspan(used);
            const auto out = std::span(reinterpret_cast<unsigned char*>(output.data()), output.size()).subspan(produced);
            const auto chunk = stream.step(segment, out);
            consumed += chunk.consumed;
            used += chunk.consumed;
            produced += chunk.produced;
        }
        if (produced != want) throw std::runtime_error("ZIP member length or CRC mismatch");
        output.resize(produced);
        return output;
    }

    std::vector<ZipCatalogMember> rows;
    std::uint64_t size = 0;
    std::size_t fetched = 0;
    unsigned requests = 0;
    std::string etag, modified, disposition;

private:
    const std::string& fetch(std::optional<std::uint64_t> offset, std::size_t length) {
        if (!length || length > budget_ - fetched || requests >= max_requests_)
            throw std::runtime_error("Archive inspection byte/request budget exhausted");
        ++requests;
        auto reply = fetch_(offset, length, etag.empty() ? modified : etag);
        fetched += reply.body.size();
        const auto first = offset.value_or(reply.range.size - std::min<std::uint64_t>(reply.range.size, length));
        if (!reply.range.size || first >= reply.range.size || reply.range.first != first ||
            reply.range.last != first + std::min<std::uint64_t>(length, reply.range.size - first) - 1 ||
            reply.body.size() != reply.range.last - first + 1)
            throw CatalogRangeChanged("Invalid HTTP byte-range response");
        if (!size) {
            size = reply.range.size;
            etag = reply.etag.starts_with("W/") ? "" : std::move(reply.etag);
            modified = std::move(reply.modified);
            disposition = std::move(reply.disposition);
        } else if (size != reply.range.size || (!etag.empty() && !reply.etag.empty() && etag != reply.etag) ||
                   (etag.empty() && !modified.empty() && !reply.modified.empty() && modified != reply.modified)) {
            throw CatalogRangeChanged("Remote archive changed during inspection");
        }
        return *spans_.insert_or_assign(first, std::make_shared<const std::string>(std::move(reply.body))).first->second;
    }

    std::string read(std::uint64_t start, std::size_t bytes) {
        if (start > size || bytes > size - start) throw std::runtime_error("ZIP range exceeds archive size");
        std::string result;
        result.reserve(bytes);
        const auto end = start + bytes;
        while (start < end) {
            auto following = spans_.upper_bound(start);
            if (following != spans_.begin()) {
                const auto span = std::prev(following);
                const auto at = start - span->first;
                if (at < span->second->size()) {
                    const auto count = std::min<std::uint64_t>(end - start, span->second->size() - at);
                    result.append(*span->second, static_cast<std::size_t>(at), static_cast<std::size_t>(count));
                    start += count;
                    continue;
                }
            }
            const auto next = following == spans_.end() ? size : following->first;
            const auto length = std::min({next - start, std::max<std::uint64_t>(8192, end - start),
                                          std::uint64_t(budget_ - fetched)});
            fetch(start, static_cast<std::size_t>(length));
        }
        return result;
    }

    CatalogFetch fetch_;
    const DeflateApi& zlib_;
    Spans spans_, directory_spans_;
    std::map<std::uint64_t, std::size_t> ordered_;
    std::uint64_t directory_ = 0;
    std::size_t budget_;
    unsigned max_requests_;
};
}
