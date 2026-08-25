"""Ragoogle domain and application layers.

Layering (ADR-0001), enforced by tools/quality/layering.py:

    domain       -> standard library only
    application  -> domain + ports
    ports        -> domain (protocol definitions, no implementations)
    adapters     -> live in ragoogle_infra, and are the only layer that may
                    import a vendor SDK
"""

__version__ = "0.1.0"
