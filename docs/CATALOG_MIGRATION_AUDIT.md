# Private native catalog migration audit (#106)

Use an immutable original closed SQLite backup. Run against the native worker
from the exact approved candidate, never upload the private catalog to CI.

    python -m tools.catalog_migration_audit --source seed.sqlite --destination working.sqlite --worker <worker> --worker-sha256 <digest> --commit <commit> --output private-migration-report.json

No network requests or automatic promotion. Exclusive destination creation,
native initialize IPC, all original table logical digests/columns preserved,
integrity and foreign keys, v2 schema and empty added tables, byte-identical
reopen, original and worker digests unchanged. Failed working copies remain
private for diagnosis. The report includes counts, not raw rows/URLs.

r339 adds synthetic v1 IPC regression tests for success, idempotent reopen,
future revision rejection, rollback on deliberate DDL conflict and identity /
overwrite refusal. A prior local Linux harness compiled exact r336 migration
header, generated SQL, pinned SQLite/config and verified the private original
30,001-release seed: all seven original table digests unchanged, three new
tables, integrity ok, FK errors zero, byte-identical reopen, future rejection
and rollback. This is not packaged Windows execution; do not promote until
the worker IPC audit is executed locally on the intended target environment.
