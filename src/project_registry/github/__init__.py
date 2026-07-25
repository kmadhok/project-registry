"""Read-only GitHub observation layer.

This package may only ever *read* from GitHub. See `client.py` for the enforced
verb restriction and MCP-006 for the reasoning.
"""

from .snapshot import Issue, PullRequest, RepoState, Snapshot  # noqa: F401
