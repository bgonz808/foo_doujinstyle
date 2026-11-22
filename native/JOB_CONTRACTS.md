# Catalog job and event contracts

The authoritative transition is:

`eligible SQLite snapshot → bounded worker fetch → SQLite commit → JSONL event → frontend presentation`.

The *fetch* portion never opens a SQLite write transaction. The *commit* portion runs on exactly one coordinator thread and serializes outcome records. A stopped job does not dispatch new batches; if Stop races a transaction already committing, that durable update still gets an event when the process survives. An asynchronous Job Object kill is stronger, but may truncate the event channel after commit. Consumers must reconcile by reading the database rather than replaying a non-idempotent POST on the assumption of failure.

Event framing is opt-in with `stream_events` (reserved, set only via Python `on_event`). Frames are newline-terminated JSON objects with either `{"event": {...}}`, or exactly one terminal `{"result": ...}` / `{"error": "..."}`. Each frame is bounded at 64 MiB and a single ReferencePublisher pipe handles at most one request. Nested or concurrent requests are rejected immediately. Cover/icon work uses separate native peers.

Foreground indexing emits one committed offer event for each committed offer (subject to process kill). The terminal result contains the same ordered events and a deduplicated copy-ID archive plan. Search emits strictly sequential `search_page` events with a page number, parsed batch and UI-date projection from committed SQLite state; the terminal result contains those same batches. Python cross-checks the terminal results against all events. Unexpected IDs, duplicates, missing commits or malformed messages are fatal and do not trigger a fallback replay.

A native HTTP 429 halts the queue; in-flight requests may nevertheless finish. A lost or uncertain POST response is recorded as an error by the current job, not retried immediately. **Cross-process exactly-once is not yet guaranteed** and requires a durable started-attempt marker or provider-supported idempotency key before retrying after worker loss.

Core fixtures: `test_catalog_index_dispatch.cpp`, `test_catalog_offer_executor.cpp`, `test_native_search_job.py`, `test_catalog_event_ipc.py`, and `test_catalog_offer_batch_bridge.py`.

## v0.0.62 archive dispatch and POST uncertainty gate

`catalog_archive_dispatch` receives an explicit, bounded list of archive copy IDs. It
revalidates each selected copy's *current* release associations and URL inside SQLite,
then uses up to three isolated C++ provider/ZIP reader instances with no SQLite
writer in network threads. The coordinator persists each archive result before
streaming the corresponding event. Cancellation prevents further dispatch; a
commit already in progress still yields one event if the process survives.
HTTP 429 halts further queue dispatch, but already in-flight results can commit.
The portable (non-Windows) worker rejects nonempty live archive work rather than
fall back into Python HTTPS. Repaired copy identity is selected again from the
catalog, including the 403 replacement-offer path.

`catalog_post_attempts` is a **durable conservative retry interlock**. Immediately
before the first outbound POST for release `R`, the native worker commits a record
`(R, private_owner, started_at, post_count)` using a separate connection. That
record survives a worker crash or timeout; another worker must not automatically
re-POST that release. Multiple forms within the same live attempt increment
`post_count`. A successful native offer-ingest transaction deletes the row
**atomically with the durable offer update**. An error, timeout, or lost terminal
does not clear the row. The public audit operation exposes only release ID,
start time and post count (never private owner). The legacy single-release
`site_release_offers` POST command is disabled, including on Windows.

This mechanism guarantees *at-most-one attempted owner until reviewed*, not
remote exactly-once delivery. A pre-POST crash can leave a false-positive
pending marker, while a provider may execute a POST whose reply never reaches
us. Operators should compare provider-side state against committed offers;
only an explicit reviewed/manual action may clear an orphaned marker. There is
no automatic expiry, replay, or retry-after-N-hours. For real exactly-once remote
semantics the provider needs compatible idempotency keys and a receipt protocol.

Fixtures: `test_catalog_archive_dispatch.cpp`, `test_post_attempt_ledger.cpp`,
`test_catalog_archive_dispatch_bridge.py`, `test_post_attempt_ipc.py`, and
`tools/test_build_identity.py`. Windows foobar2000 networking, Windows Job Object
hard Stop and runtime UI callbacks remain unqualified until host testing.
