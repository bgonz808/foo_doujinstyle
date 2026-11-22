#pragma once

#include "catalog_tags.hpp"

namespace doujin {

inline std::optional<std::vector<std::string>> catalog_status(const std::string& path,
                                                              std::stop_token stop = {}) {
    // One SELECT pins a read snapshot for the duration of the report.
    constexpr char query[] = R"sql(
        WITH active AS (
            SELECT DISTINCT copy_id, release_id FROM release_copies WHERE current=1
        ), archives AS (
            SELECT id, COALESCE(archive, '{}') AS a FROM copies
            WHERE id IN (SELECT copy_id FROM active)
        ), layouts AS (
            SELECT CASE
                WHEN json_extract(a,'$.format')='zip' AND EXISTS (
                    SELECT 1 FROM json_each(a,'$.inventory')
                    WHERE json_extract(value,'$.kind')='audio_candidate'
                      AND json_extract(value,'$.zip_method')=8 AND json_extract(value,'$.encrypted') IS NOT 1
                      AND json_extract(value,'$.uncompressed_bytes') > 4194304
                ) THEN 'ZIP with DEFLATE audio over 4 MiB'
                WHEN json_extract(a,'$.format')='zip' AND EXISTS (
                    SELECT 1 FROM json_each(a,'$.inventory') WHERE json_extract(value,'$.kind')='audio_candidate'
                ) AND NOT EXISTS (
                    SELECT 1 FROM json_each(a,'$.inventory')
                    WHERE json_extract(value,'$.kind')='audio_candidate'
                      AND (json_extract(value,'$.zip_method') IS NOT 0 OR json_extract(value,'$.encrypted') IS 1)
                ) THEN 'ZIP with STORE-only audio'
                WHEN json_extract(a,'$.format')='zip' THEN 'ZIP with other or unclassified audio'
                WHEN json_extract(a,'$.format') IN ('7z','rar','rar5') THEN '7z/RAR, solid status unknown'
                WHEN json_extract(a,'$.format') IS NOT NULL THEN 'Other inspected format'
                ELSE 'Format unobserved'
            END AS label FROM archives
        ), probes AS (
            SELECT archives.id, CASE WHEN p.key=json_extract(a,'$.audio_sample.path')
                       THEN json_patch(json_extract(a,'$.audio_sample'), p.value) ELSE p.value END AS p
            FROM archives, json_each(a,'$.audio_probes') AS p
            UNION ALL
            SELECT id, json_extract(a,'$.audio_sample') FROM archives
            WHERE COALESCE(json_extract(a,'$.audio_sample.path'),'') <> '' AND NOT EXISTS (
                SELECT 1 FROM json_each(a,'$.audio_probes')
                WHERE key=json_extract(a,'$.audio_sample.path'))
        ), counts AS (
            SELECT id, count(*) AS headers,
                   sum(EXISTS(SELECT 1 FROM json_each(p,'$.metadata')
                              WHERE json_type(p,'$.metadata') IN ('object','array'))) AS technical,
                   sum(EXISTS(SELECT 1 FROM json_each(p,'$.tags')
                              WHERE json_type(p,'$.tags') IN ('object','array'))) AS tags,
                   sum(COALESCE(json_array_length(p,'$.artwork'),0)) AS embedded
            FROM probes GROUP BY id
        ), coverage(layer, id, amount) AS (
            SELECT 4, id, 1 FROM archives
            UNION ALL SELECT 5, id, COALESCE(json_extract(a,'$.audio_members'), json_array_length(a,'$.audio'), 0)
                FROM archives WHERE json_extract(a,'$.format')='zip'
                AND (json_type(a,'$.members') IS NOT NULL OR json_type(a,'$.inventory') IS NOT NULL)
            UNION ALL SELECT 6, id, headers FROM counts WHERE headers>0
            UNION ALL SELECT 7, id, technical FROM counts WHERE technical>0
            UNION ALL SELECT 8, id, tags FROM counts WHERE tags>0
            UNION ALL SELECT 9, archives.id,
                COALESCE(json_array_length(a,'$.artwork'), (
                    SELECT count(*) FROM json_each(a,'$.inventory') WHERE json_extract(value,'$.kind')='artwork'
                ))+COALESCE(embedded,0) FROM archives LEFT JOIN counts USING(id)
            UNION ALL SELECT 10, id, 1 FROM archives WHERE COALESCE(json_extract(a,'$.cached_front_cover'),'')<>''
        ), layers(layer, label, unit) AS (
            VALUES (4,'Download links','copies'), (5,'ZIP directories','audio members'),
                   (6,'Headers probed','members'), (7,'Technical metadata','members'),
                   (8,'Track tags','members'), (9,'Cover candidates','candidates'), (10,'Front covers recorded',NULL)
        ), listed AS (
            SELECT id, seen, listing, json_extract(listing,'$.listed_at') AS added FROM releases
        ), dates AS (
            SELECT id, added FROM listed WHERE length(added)=10 AND date(added,'+0 days')=added
        ), bytes(idx, n) AS (
            SELECT 0, (SELECT page_size FROM pragma_page_size)*(SELECT page_count FROM pragma_page_count)
            UNION ALL SELECT 1, COALESCE(sum(length(CAST(snapshot AS blob))),0) FROM pages
            UNION ALL SELECT 2, COALESCE(sum(length(CAST(listing AS blob))),0) FROM releases
            UNION ALL SELECT 3, COALESCE(sum(length(CAST(offer AS blob))),0) FROM copies
            UNION ALL SELECT 4, COALESCE(sum(length(CAST(archive AS blob))),0) FROM copies
            UNION ALL SELECT 5, COALESCE(sum(pgsize),0) FROM dbstat
                              WHERE name IN (SELECT name FROM sqlite_schema WHERE type='index')
        ), sizes AS (
            SELECT idx, CASE WHEN n<1024 THEN printf('%,d B',n)
                WHEN n<1048576 THEN printf('%.2f KiB',n/1024.0)
                WHEN n<1073741824 THEN printf('%.2f MiB',n/1048576.0)
                ELSE printf('%.2f GiB',n/1073741824.0) END AS label FROM bytes
        ), report(position, line) AS (
            SELECT 0, printf('Catalog listings  •  %,d releases on %,d cached pages',
                             count(*), (SELECT count(*) FROM pages)) FROM listed
            UNION ALL SELECT 1, printf('Observed release IDs  •  %d–%d',min(id),max(id))
                      FROM listed HAVING count(*)>0
            UNION ALL SELECT 2, 'Browse backfill  •  ' || CASE
                (SELECT json_extract(snapshot,'$.complete') FROM backfill_checkpoint WHERE id=1)
                WHEN 1 THEN 'Reached newest page' WHEN 0 THEN 'In progress' ELSE 'No checkpoint' END
            UNION ALL SELECT 3, 'Last catalog observation  •  ' || datetime(max(seen),'unixepoch') || ' UTC'
                      FROM listed HAVING count(*)>0
            UNION ALL SELECT 4, 'Date Added  •  ' ||
                (SELECT printf('%s (ID %d)',added,id) FROM dates ORDER BY added,id LIMIT 1) || ' to ' ||
                (SELECT printf('%s (ID %d)',added,id) FROM dates ORDER BY added DESC,id DESC LIMIT 1) ||
                printf('  •  %,d dated',count(*)) FROM dates HAVING count(*)>0
            UNION ALL SELECT 5, printf('Release details  •  %,d releases',count(*)) FROM listed
                WHERE json_type(listing,'$.listed_at') IS NOT NULL OR
                      json_type(listing,'$.updated_at') IS NOT NULL OR json_type(listing,'$.download_offer') IS NOT NULL
            UNION ALL SELECT layer+2, label || printf('  •  %,d releases', (
                SELECT count(DISTINCT active.release_id) FROM coverage JOIN active ON active.copy_id=coverage.id
                WHERE coverage.layer=layers.layer AND (coverage.amount>0 OR coverage.layer=5)
            )) || CASE WHEN unit IS NULL THEN '' ELSE printf('  •  %,d %s', (
                SELECT COALESCE(sum(amount),0) FROM coverage WHERE coverage.layer=layers.layer
            ),unit) END FROM layers
            UNION ALL SELECT 13, 'SQLite  •  ' || (SELECT label FROM sizes WHERE idx=0)
            UNION ALL SELECT 14, 'Payload sizes  •  pages ' || (SELECT label FROM sizes WHERE idx=1) ||
                '  ·  listings ' || (SELECT label FROM sizes WHERE idx=2) ||
                '  ·  offers ' || (SELECT label FROM sizes WHERE idx=3) ||
                '  ·  archives ' || (SELECT label FROM sizes WHERE idx=4)
            UNION ALL SELECT 15, 'SQLite indexes  •  ' || (SELECT label FROM sizes WHERE idx=5) ||
                printf(' across %d indexes', (SELECT count(*) FROM sqlite_schema WHERE type='index'))
            UNION ALL SELECT 16, 'SQLite journal  •  ' || upper(journal_mode) FROM pragma_journal_mode
            UNION ALL SELECT 17, printf('Archive layouts  •  %,d current catalog copies',count(*)) FROM layouts
            UNION ALL SELECT 18, printf('%s  •  %,d / %,d (%.1f%%)', label, count(*),
                (SELECT count(*) FROM layouts), 100.0 * count(*) / (SELECT count(*) FROM layouts))
                FROM layouts GROUP BY label
        )
        SELECT line FROM report WHERE line IS NOT NULL ORDER BY position
    )sql";
    return catalog_strings(path, query, stop);
}

} // namespace doujin
