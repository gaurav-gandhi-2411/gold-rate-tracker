"""Retail gold-price source adapters feeding the fusion engine (ADR 026).

Each adapter implements the :class:`~ml.sources.base.SourceAdapter` protocol:
``fetch()`` returns a :class:`~ml.sources.base.SourceReading` or raises a
:class:`~ml.sources.base.SourceNetworkError` /
:class:`~ml.sources.base.SourceStructureError`. Adding a new source is writing
a module here and registering it in ``ml/shadow_fusion.py`` — the fusion
engine (``ml/fusion.py``) never changes.
"""

from __future__ import annotations
