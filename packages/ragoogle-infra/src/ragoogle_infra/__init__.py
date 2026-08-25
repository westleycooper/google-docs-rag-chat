"""Ragoogle adapters.

The only layer permitted to import a vendor SDK (ADR-0001). Everything here
implements a Protocol from `ragoogle_core.ports` and is wired at the composition
root in `ragoogle_api`.
"""

__version__ = "0.1.0"
