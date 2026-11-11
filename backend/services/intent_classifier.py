"""Deprecated — soft prototype blend removed (Ockham).

Kept as an empty module so old imports fail loudly with a clear message
rather than mysterious AttributeErrors. Do not reintroduce blend scoring.
"""
from __future__ import annotations


def score_prototypes(message: str):  # noqa: ARG001
    raise RuntimeError(
        "intent_classifier.score_prototypes was removed — "
        "intent_router uses hard detectors + sticky state + RAG default only."
    )


def blend_scores(rule, proto, **_kwargs):  # noqa: ARG001
    raise RuntimeError(
        "intent_classifier.blend_scores was removed — "
        "no soft prototype blend in the live router."
    )
