"""Project Registry: a control plane for understanding a project portfolio.

Human intent lives in ``registry/`` and is authoritative. Observed GitHub state
lives in ``data/`` and is evidence only. No code path lets the second overwrite
the first.
"""

__version__ = "0.1.0"

from .model import Lifecycle, Project  # noqa: F401
from .storage import Paths, Registry, load_registry  # noqa: F401
