# Incremental catalog census (#106, #135)

Revision 337: closed-backup-only, private offline census planner; no live network
or production runtime changes. Freeze seed/population once, reuse the manifest
against subsequent backups without substituting failed IDs. The legacy seed
algorithm matches the existing 2026-11-26 manifest.

    python -m tools.catalog_census --db seed.sqlite --freeze-seed foo_doujinstyle-census-v1-2026-11-26 --output private-manifest.json
    python -m tools.catalog_census --db backup.sqlite --manifest private-manifest.json --size 100 --batch 10 --output private-report.json
    python -m tools.catalog_census --db next-backup.sqlite --manifest private-manifest.json --size 1000 --batch 10 --output private-next.json

Outputs contain private release/copy IDs and host-family names; never publish raw
DB, manifest or report. Publish reviewed aggregates only. Current vs historical
associations distinct; deduplicate multiple sources pointing to one copy. Count
host x archive x method x audio suffix x encryption with separate evidence grades,
do not infer codec or playback. Rank observed gaps by affected distinct releases
and copies, including other known structural candidates and unresolved alternatives.
These are overlapping impact indicators, not additive or causal gains.

Next batch only never-checked offer IDs and never-checked eligible known-provider
copies; no automatic error retries. Existing native catalog_offer_batch and
catalog_archive_dispatch are the intended execution paths (workers=1, header/cover
false initially), NOT new Python runtime or direct scraper. Validate current
native catalog_index_plan and provider policy before dispatch, preserve SQLite
commit and uncertain POST markers. Existing native schema rejects newer versions;
actual legacy migration on disposable backup, logical row preservation and
idempotent reopen still required. Native archive dispatch currently hardcodes
2 MiB and 12 requests per copy; enforce cumulative and per-origin budgets and
429 pause before enabling a persistent live lane. Use private durable local
volume, one writer, backup/restore and disposable candidate build snapshots.
Hosted ephemeral CI is not a deployed long-lived VM. No live acquisition yet.

Revision 338 reconciles r337 with merged playback matrix r336; both test suites retained.
