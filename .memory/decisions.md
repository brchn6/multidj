# Decisions Log

## 2026-06-04 — Doc sync: Mixxx config, CLI fallback, source-of-truth, idempotency

- **`[mixxx]` config in DEFAULT_CONFIG:** Decision to keep the pattern consistent with existing `[db].path` and `[pipeline].music_dir` — a `[mixxx]` section with a single `path` key, with a dedicated `get_mixxx_db_path(cfg)` getter that returns `str | None`.
- **CLI fallback pattern:** Decision to use `args.mixxx_db or get_mixxx_db_path(cfg)` in all four Mixxx-using commands (`pipeline`, `sync mixxx`, `import mixxx`, `analyze mixxx-blobs`). Consistent with how `get_music_dir()` is already used. CLI `--mixxx-db` always wins over config.
- **Sync notes go at newest-first order:** Added the 2026-06-03 section before the existing 2026-05-31/27 sections in all three doc files, maintaining the convention that newer notes appear first.
- **No content removed from memory files:** This is the initial creation — both progress.md and decisions.md are seeded with their first entry.
