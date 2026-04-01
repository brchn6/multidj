# Agent Handoff Protocol

Sub-agents in this project write handoff files here before their context fills or when done.

## Files
- layer1a.md — Test scaffold (Sub-agent A)
- layer1b.md — import mixxx (Sub-agent B)
- layer2c.md — scan/audit/enrich port (Sub-agent C)
- layer2d.md — parse/clean port (Sub-agent D)
- layer2e.md — crates port (Sub-agent E)
- layer2f.md — dedupe/analyze port (Sub-agent F)
- layer3g.md — sync mixxx (Sub-agent G)

## Format
Each file: Status, Completed, Decisions Made, Remaining, Next Agent Prompt.
New agents: read the handoff file first, never redo completed work.
