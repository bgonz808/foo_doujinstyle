CREATE TABLE IF NOT EXISTS pages (
    url TEXT PRIMARY KEY, snapshot TEXT NOT NULL, fetched INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS backfill_checkpoint (
    id INTEGER PRIMARY KEY CHECK (id=1), snapshot TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS releases (
    id INTEGER PRIMARY KEY, listing TEXT NOT NULL, seen INTEGER NOT NULL,
    offer TEXT, share_url TEXT, host TEXT, offer_checked INTEGER, offer_error TEXT,
    archive TEXT, archive_checked INTEGER, archive_error TEXT
);
CREATE TABLE IF NOT EXISTS copies (
    id INTEGER PRIMARY KEY, destination TEXT UNIQUE NOT NULL,
    share_url TEXT NOT NULL, host TEXT NOT NULL, offer TEXT NOT NULL,
    archive TEXT, archive_checked INTEGER, archive_error TEXT
);
CREATE TABLE IF NOT EXISTS release_copies (
    release_id INTEGER NOT NULL REFERENCES releases(id),
    copy_id INTEGER NOT NULL REFERENCES copies(id),
    source TEXT NOT NULL,
    first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL,
    current INTEGER NOT NULL CHECK (current IN (0,1)),
    PRIMARY KEY (release_id, copy_id, source)
);
CREATE INDEX IF NOT EXISTS release_copies_by_copy ON release_copies(copy_id);
CREATE TABLE IF NOT EXISTS listing_activity (
    release_id INTEGER PRIMARY KEY REFERENCES releases(id),
    discovered_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tracklists (
    release_id INTEGER NOT NULL REFERENCES releases(id),
    source_url TEXT NOT NULL,
    tracks TEXT NOT NULL,
    PRIMARY KEY (release_id, source_url)
);
CREATE TABLE IF NOT EXISTS catalog_lookup_claims (
    provider TEXT NOT NULL, release_id INTEGER NOT NULL REFERENCES releases(id),
    token TEXT NOT NULL, expires INTEGER NOT NULL, PRIMARY KEY (provider, release_id)
);
CREATE TABLE IF NOT EXISTS published_references (
    root TEXT NOT NULL, filename TEXT NOT NULL, path TEXT NOT NULL,
    PRIMARY KEY (root, filename)
);
-- Created lazily for preexisting v2 catalogs; mandatory before any POST.
CREATE TABLE IF NOT EXISTS catalog_post_attempts (
    release_id INTEGER PRIMARY KEY,
    owner TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    post_count INTEGER NOT NULL CHECK(post_count >= 1)
);
