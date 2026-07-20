# Operating the Project Registry

## Routine refresh

Run:

```bash
python -m project_registry refresh --owner kmadhok
```

The command performs three gates in order:

1. Read repositories, open PRs, open issues, and PR check/review state through authenticated `gh`.
2. Merge newly observed repositories while preserving all existing curated fields.
3. Validate the materialized registry before regenerating `DASHBOARD.md`.

The snapshot records its refresh timestamp and any non-fatal PR-enrichment warnings. A failed base GitHub query does not replace the last known-good snapshot.

## Owner review loop

Use the dashboard's `recent-needs-review`, PR, and missing-action signals as a review queue—not as an automatic priority system.

For each reviewed project:

1. Read any existing understanding brief and its unanswered questions.
2. Confirm or correct the purpose.
3. Select a lifecycle deliberately.
4. Decide whether it is active.
5. If active, record one next action and a last-reviewed timestamp.
6. If `now`, record the outcome that moves it out of `now`.
7. Connect predecessors, successors, duplicates, and components in `data/projects.json`.
8. Regenerate and validate the dashboard.

Use `set-project` for common fields. Relationship and accomplishment arrays remain deliberate JSON edits until their workflow is mature enough for a dedicated command.

## Repository understanding pass

Follow [REPOSITORY_UNDERSTANDING.md](REPOSITORY_UNDERSTANDING.md). A complete pass should:

- identify the analyzed commit;
- record inspected, missing, inaccessible, and sampled evidence;
- separate stated purpose, observed implementation, and planned work;
- explain the main architecture and operating path;
- record accomplishments, gaps, risks, and suggested actions with evidence IDs;
- assign maturity independently from lifecycle;
- record confidence, limitations, and owner questions;
- avoid reading or storing credential values.

Use `scaffold-understanding` only to create the evidence inventory. Its placeholders are not a completed analysis.

## Validation rules

The validator rejects, among other conditions:

- duplicate repository records or project IDs;
- a GitHub repository missing from the registry;
- reviewed projects without purpose or lifecycle;
- active projects without a next action and review timestamp;
- `now` projects without a desired outcome;
- superseded projects without a named successor;
- relationships targeting unknown projects;
- missing or mismatched understanding files;
- invalid maturity, confidence, claim type, or evidence references.

Run both checks before publishing:

```bash
python -m project_registry validate
python -m project_registry dashboard --check
```

## Failure recovery

- **GitHub command failure:** fix authentication or connectivity, then rerun. Existing data remains available.
- **PR enrichment warning:** the snapshot is usable, but the affected PR has unknown check/review details; rerun after resolving access.
- **Validation failure:** no dashboard should be treated as current until the listed invariant is fixed.
- **Stale dashboard:** regenerate it from the current validated data.
- **Incorrect generated understanding:** correct the brief with cited evidence; do not silently convert the claim into owner context.

## Privacy

This repository is private and its dashboard may name private repositories. Any future public portfolio export must use only explicitly approved public-safe fields and must not reuse this dashboard directly.
