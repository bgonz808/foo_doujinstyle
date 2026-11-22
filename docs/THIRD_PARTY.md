# Third-party source inventory (pre-release review)

- foobar2000 SDK: **not vendored**. Build uses an official SDK source archive
  supplied separately, pinned to the 2026-10-01 baseline with SHA-256
  `d4c55077336fae81bf8df0259b5b2748fa45ea84132c656ead93eb123cbcdc26`.
  Its original license applies to SDK headers and compilation units.
- SQLite 3.54.1: `native/sqlite-amalgamation-3540100.zip` is the pinned
  upstream source archive for offline compilation. SQLite is public domain;
  do not confuse its ZIP with a compiled output or incidental cache.
- nlohmann/json: `native/third_party/json.hpp`, MIT; see
  `native/third_party/LICENSE.nlohmann-json`.
- utf8proc: `native/third_party/utf8proc/`, MIT plus required Unicode-data notices;
  see its `LICENSE.md`.
- zlib: interface headers in `native/zlib/`; the component normally uses
  the host's zlib. Review any separately distributed zlib source or DLL license.

A dependency review is required before promoting any WIP binary to a public
release. The MIT root license covers first-party code, not third-party code or
music/artwork from upstream providers.
