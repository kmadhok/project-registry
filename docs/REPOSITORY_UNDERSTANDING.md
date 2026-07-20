# Repository Understanding Process

The registry must explain what a repository actually contains and implements, not merely repeat its name, description, or README.

## Evidence pass

For each analyzed revision, inspect the available evidence in this order:

1. Repository metadata, default branch, visibility, archival state, and revision.
2. README, specifications, plans, architecture notes, and setup documentation.
3. Top-level structure, entry points, dependency manifests, configuration, and data stores.
4. Representative source paths and the connections between major components.
5. Tests, CI workflows, releases, deployable artifacts, notebooks, demos, and generated outputs.
6. Recent commit history, active branches, open PRs, and relevant issues.
7. Evidence of security, dependency, generated-file, or operational risks without reading secret values.

Large repositories may be sampled. The brief must state what was sampled and what was not inspected.

## Claim discipline

Every material conclusion is one of:

- `observed_fact`: directly supported by inspected evidence;
- `supported_inference`: a reasoned conclusion with cited evidence;
- `owner_context`: information supplied by the owner and not independently proven by the repository.

Stated purpose, observed implementation, and planned work remain separate. Missing evidence becomes a limitation or owner question, not a guessed fact.

## Maturity vocabulary

| Level | Meaning |
|---|---|
| `empty` | No substantive tracked implementation. |
| `concept` | Intent or planning exists without a usable implementation. |
| `scaffold` | Structure or generated foundation exists, but core behavior is incomplete. |
| `prototype` | Core behavior is demonstrated with meaningful gaps or manual steps. |
| `functional` | The primary workflow works and has supporting evidence. |
| `operational` | The project is used or deployable with repeatable setup and operational safeguards. |
| `mature` | The project is operational, well-tested, documented, and intentionally maintained. |

Maturity describes implementation evidence. It does not decide lifecycle or priority.

## Confidence

- `high`: primary implementation evidence and verification artifacts agree.
- `medium`: the main conclusion is supported, but important execution or coverage evidence is missing.
- `low`: evidence is sparse, contradictory, inaccessible, or mostly aspirational.

## Refresh rule

Each brief records the analyzed commit. A refresh compares that commit with the current default-branch revision, focuses on changed evidence, preserves still-valid findings, and highlights changed conclusions. Prior briefs remain available through Git history.

## Safety

Analysis is read-only. It may identify credential filenames or configuration requirements, but it must never read, copy, summarize, or store credential values. Suggested lifecycle, relationship, and priority changes require owner review.
