#pragma once

#include "descriptor.hpp"
#include <algorithm>
#include <list>

namespace doujin {
// One index per validated archive session. Callers serialize lookups.
class ZipMemberCache {
public:
    explicit ZipMemberCache(std::size_t capacity = 256) : capacity_(capacity) {}

    std::optional<ZipMember> locate(std::string_view name, const std::optional<ZipLocator>& locator,
                                    const auto& resolve) {
        constexpr std::size_t key_limit = 4096;
        if (!capacity_ || name.size() > key_limit || (locator && locator->raw_name.size() > key_limit - name.size()))
            return resolve();
        const auto found = std::find_if(members_.begin(), members_.end(), [&](const auto& item) {
            return item.name == name && item.locator == locator;
        });
        if (found != members_.end()) {
            members_.splice(members_.begin(), members_, found);
            return members_.front().member;
        }
        const auto resolved = resolve();
        if (resolved) {
            members_.push_front({std::string(name), locator, *resolved});
            if (members_.size() > capacity_) members_.pop_back();
        }
        return resolved;
    }

private:
    struct Entry {
        std::string name;
        std::optional<ZipLocator> locator;
        ZipMember member;
    };
    std::size_t capacity_;
    std::list<Entry> members_;
};
}
