"""MCP server exposing the registry to agents.

Read tools cover MCP-001 through MCP-004. Write tools (MCP-005) are limited to
the registry's own curated fields and split into propose/apply. No tool in this
package mutates anything on GitHub (MCP-006).
"""

from .server import TOOLS, handle_request, serve  # noqa: F401
