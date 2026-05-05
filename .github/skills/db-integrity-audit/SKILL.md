---
name: db-integrity-audit
description: Run a fixed SQL integrity audit for the MultiDJ SQLite database and return pass/fail with threshold checks.
---

# DB Integrity Audit Skill

## Outcomeת

Produce a deterministic PASS or FAIL integrity result for the MultiDJ DB using fixed SQL checks and fixed thresholds.

## Scope

- Workspace-scoped for this repository.
- Database target is the MultiDJ DB only (default `~/.multidj/library.sqlite` unless overridden).
- Read-only auditing only. Do not mutate DB state.

## Inputs

- `db_path` (optional): path to MultiDJ DB.
- If omitted, resolve from CLI config via `multidj config set-db` conventions and default path.

## Preconditions

1. Confirm the DB path is not Mixxx DB.
2. Ensure the DB file exists.
3. Use read-only commands only.

## Fixed Checks

Run each SQL query exactly as listed. Capture the numeric result or string result.

1. Structural integrity

- Command:
  - `sqlite3 <DB_PATH> "PRAGMA integrity_check;"`
- Pass condition:
  - Result equals `ok`.

2. Foreign key integrity

- Command:
  - `sqlite3 <DB_PATH> "PRAGMA foreign_key_check;" | wc -l`
- Pass condition:
  - Count equals `0`.

3. Active track floor

- Command:
  - `sqlite3 <DB_PATH> "SELECT COUNT(*) FROM tracks WHERE deleted=0;"`
- Pass condition:
  - Count is greater than or equal to `1`.

4. Duplicate active paths

- Command:
  - `sqlite3 <DB_PATH> "SELECT COUNT(*) FROM (SELECT path FROM tracks WHERE deleted=0 GROUP BY path HAVING COUNT(*) > 1);"`
- Pass condition:
  - Count equals `0`.

5. Orphan crate memberships

- Command:
  - `sqlite3 <DB_PATH> "SELECT COUNT(*) FROM crate_tracks ct LEFT JOIN crates c ON c.id = ct.crate_id LEFT JOIN tracks t ON t.id = ct.track_id WHERE c.id IS NULL OR t.id IS NULL;"`
- Pass condition:
  - Count equals `0`.

6. Orphan sync_state rows

- Command:
  - `sqlite3 <DB_PATH> "SELECT COUNT(*) FROM sync_state s LEFT JOIN tracks t ON t.id = s.track_id WHERE t.id IS NULL;"`
- Pass condition:
  - Count equals `0`.

7. Invalid soft-delete values

- Command:
  - `sqlite3 <DB_PATH> "SELECT COUNT(*) FROM tracks WHERE deleted NOT IN (0,1);"`
- Pass condition:
  - Count equals `0`.

8. Invalid energy range

- Command:
  - `sqlite3 <DB_PATH> "SELECT COUNT(*) FROM tracks WHERE deleted=0 AND energy IS NOT NULL AND (energy < 0 OR energy > 1);"`
- Pass condition:
  - Count equals `0`.

9. Invalid BPM values

- Command:
  - `sqlite3 <DB_PATH> "SELECT COUNT(*) FROM tracks WHERE deleted=0 AND bpm IS NOT NULL AND bpm <= 0;"`
- Pass condition:
  - Count equals `0`.

## Metadata Coverage Thresholds

Use active tracks only (`deleted=0`). All percentages below are minimum PASS thresholds.

1. Artist coverage

- Command:
  - `sqlite3 <DB_PATH> "SELECT ROUND(100.0 * AVG(CASE WHEN artist IS NOT NULL AND TRIM(artist) <> '' THEN 1.0 ELSE 0.0 END), 2) FROM tracks WHERE deleted=0;"`
- Pass condition:
  - Value is greater than or equal to `90.0`.

2. Title coverage

- Command:
  - `sqlite3 <DB_PATH> "SELECT ROUND(100.0 * AVG(CASE WHEN title IS NOT NULL AND TRIM(title) <> '' THEN 1.0 ELSE 0.0 END), 2) FROM tracks WHERE deleted=0;"`
- Pass condition:
  - Value is greater than or equal to `90.0`.

3. Genre coverage

- Command:
  - `sqlite3 <DB_PATH> "SELECT ROUND(100.0 * AVG(CASE WHEN genre IS NOT NULL AND TRIM(genre) <> '' THEN 1.0 ELSE 0.0 END), 2) FROM tracks WHERE deleted=0;"`
- Pass condition:
  - Value is greater than or equal to `35.0`.

4. BPM coverage

- Command:
  - `sqlite3 <DB_PATH> "SELECT ROUND(100.0 * AVG(CASE WHEN bpm IS NOT NULL THEN 1.0 ELSE 0.0 END), 2) FROM tracks WHERE deleted=0;"`
- Pass condition:
  - Value is greater than or equal to `30.0`.

5. Key coverage

- Command:
  - `sqlite3 <DB_PATH> "SELECT ROUND(100.0 * AVG(CASE WHEN key IS NOT NULL AND TRIM(key) <> '' THEN 1.0 ELSE 0.0 END), 2) FROM tracks WHERE deleted=0;"`
- Pass condition:
  - Value is greater than or equal to `30.0`.

6. Energy coverage

- Command:
  - `sqlite3 <DB_PATH> "SELECT ROUND(100.0 * AVG(CASE WHEN energy IS NOT NULL THEN 1.0 ELSE 0.0 END), 2) FROM tracks WHERE deleted=0;"`
- Pass condition:
  - Value is greater than or equal to `30.0`.

## Decision Logic

1. If any structural check fails, overall result is FAIL.
2. If any data-validity check fails, overall result is FAIL.
3. If any coverage threshold fails, overall result is FAIL.
4. If all checks pass, overall result is PASS.

## Remediation Branch (when FAIL)

Run in this order, then re-run this full audit:

1. `multidj pipeline --music-dir ~/Music/All_Tracks`
2. If dry-run output is acceptable and user approved writes: `multidj pipeline --apply --music-dir ~/Music/All_Tracks`
3. `multidj audit metadata`
4. Re-run all SQL checks in this skill.

## Completion Criteria

- Every fixed check passes.
- Every coverage threshold passes.
- Final output includes:
  - Overall PASS/FAIL
  - Failed checks list (if any)
  - Exact observed values for each check

## Output Format

Use this compact structure:

- `overall`: `PASS` or `FAIL`
- `db_path`: resolved path
- `checks`: list of `{name, observed, threshold, status}`
- `coverage`: list of `{field, observed_pct, threshold_pct, status}`
- `next_actions`: empty on PASS, remediation steps on FAIL

## References

- [AGENTS.md](AGENTS.md)
- [README.md](README.md)
- [docs/superpowers/specs/2026-04-22-pipeline-design.md](docs/superpowers/specs/2026-04-22-pipeline-design.md)
