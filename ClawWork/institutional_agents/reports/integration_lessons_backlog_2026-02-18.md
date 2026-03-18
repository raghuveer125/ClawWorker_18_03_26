# Integration Lessons Learned & Backlog

Date: 2026-02-18

## Lessons Learned
- Markdown links pasted into terminal caused repeated shell syntax errors; plain-text commands are required.
- Placeholder tokens (`<...>`) in shell commands were interpreted as redirection; docs now include safe usage warnings.
- Shadow-first integration enabled safe progress without order-path risk.
- Evidence automation (daily summaries, observability, gate evaluator, rollout tracker) reduced manual ambiguity in checklist closure.

## Backlog Items
- Add one-command wrapper for most-used integration operations (seed -> summarize -> gate -> rollout check).
- Add optional real screener ingestion into observability success-rate metrics for richer adapter health calculations.
- Add owner-signoff capture workflow (structured JSON/markdown with explicit signature status).
- Add release-tag automation hook gated behind explicit user approval.
- Add CI checks to verify all checklist-linked artifact paths exist.
