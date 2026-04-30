# AGENTS.md

Agent operating guide for this repository.

## Start Here

- Read [README.md](README.md) for command behavior and CLI surface.
- Read [CLAUDE.md](CLAUDE.md) for architecture, safety model, and module map.
- For pipeline details, read [docs/superpowers/specs/2026-04-22-pipeline-design.md](docs/superpowers/specs/2026-04-22-pipeline-design.md).
- Read [.agent-handoff/README.md](.agent-handoff/README.md) before coding in areas touched by prior sub-agents.

## Handoff Protocol

- Review the relevant handoff file first and do not redo work already marked complete.
- Use handoff files to capture status, decisions, remaining work, and the next-agent prompt when splitting tasks.
- Handoff index: [.agent-handoff/README.md](.agent-handoff/README.md)
- Current handoff files:
  - [.agent-handoff/layer1a.md](.agent-handoff/layer1a.md)
  - [.agent-handoff/layer1b.md](.agent-handoff/layer1b.md)
  - [.agent-handoff/layer2c.md](.agent-handoff/layer2c.md)
  - [.agent-handoff/layer2d.md](.agent-handoff/layer2d.md)
  - [.agent-handoff/layer2e.md](.agent-handoff/layer2e.md)
  - [.agent-handoff/layer2f.md](.agent-handoff/layer2f.md)
  - [.agent-handoff/layer3g.md](.agent-handoff/layer3g.md)

## Environment and Commands

- Python: 3.9+
- Install:
  - `python3 -m venv .venv`
  - `.venv/bin/pip install -e .`
  - `.venv/bin/pip install -r requirements-dev.txt`
- Main CLI entrypoint: `multidj` (legacy alias: `mixxx-tool`)
- Run tests:
  - `.venv/bin/pytest tests/ -v`
  - `.venv/bin/pytest tests/test_pipeline.py -v`

## Critical Invariants

- MultiDJ DB is the source of truth, not Mixxx.
- Write flows are dry-run by default and require `--apply`.
- Backups are expected before writes unless explicitly skipped.
- Soft-delete semantics must be preserved (`tracks.deleted = 1`), not hard delete.
- Active-track logic must consistently exclude deleted rows (`deleted = 0`).
- Analyze commands should keep per-track error isolation.

## Codebase Landmarks

- CLI dispatch and global flag hoisting: [multidj/cli.py](multidj/cli.py)
- DB connect/migrations/guards: [multidj/db.py](multidj/db.py)
- Backup flow: [multidj/backup.py](multidj/backup.py)
- End-to-end pipeline orchestrator: [multidj/pipeline.py](multidj/pipeline.py)
- Mixxx sync adapter: [multidj/adapters/mixxx.py](multidj/adapters/mixxx.py)
- Directory import adapter: [multidj/adapters/directory.py](multidj/adapters/directory.py)
- Crate logic and protection model: [multidj/crates.py](multidj/crates.py)
- Canonical fixture data for tests: [tests/fixtures/data.py](tests/fixtures/data.py)

## Change Guidance

- Prefer minimal, targeted edits and preserve current CLI/API behavior.
- Keep JSON output contracts stable when changing command responses.
- When changing DB writes, add or update tests in [tests/](tests) that verify dry-run safety and apply behavior.
- If touching pipeline behavior, update or add assertions in [tests/test_pipeline.py](tests/test_pipeline.py).

## Common Gotchas

- This repo may be run against the wrong DB path; commands often guard against Mixxx DB usage.
- `pipeline --apply` should take one backup at start, not one per step.
- Crate sync to Mixxx reconciles stale auto crates and membership; avoid partial sync logic changes unless fully tested.
