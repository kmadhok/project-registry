# Project Registry

A private control plane for understanding, prioritizing, and maintaining Kanu's project portfolio.

The registry combines two kinds of information:

- **Human intent:** why a project exists, whether it is active, what outcome matters, and what should happen next.
- **GitHub evidence:** open pull requests, issues, recent activity, CI state, and other repository signals.

Human intent remains authoritative. Automation may surface evidence and suggest actions, but it must not silently change priorities, close work, or archive repositories.

## Current phase

This repository is in the **foundation** phase. The immediate goals are to:

1. Define the registry's purpose and boundaries.
2. Define what the owner should be able to do.
3. Define what a future MCP server should be able to do.
4. Use those definitions to design the data model and first GitHub synchronization workflow.

## Documents

- [Purpose and operating model](docs/PURPOSE.md)
- [User stories and MCP capabilities](docs/USER_STORIES.md)

## Planned evolution

1. **Foundation:** purpose, vocabulary, user stories, and safety boundaries.
2. **Registry:** a machine-readable project catalog plus a generated human-readable dashboard.
3. **GitHub sync:** read-only collection of repositories, PRs, issues, activity, and CI signals.
4. **Decision support:** prioritized views of active work, stale work, and missing next actions.
5. **MCP server:** tools that let agents query and maintain the registry with explicit write controls.

## Non-goals for the first version

- Replacing GitHub as the source of repository and pull-request data.
- Automatically deciding which projects matter.
- Automatically closing issues, merging PRs, deleting code, or archiving repositories.
- Storing credentials, source code from other projects, or private document contents.
