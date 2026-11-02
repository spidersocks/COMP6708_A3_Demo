"""Tiny in-process cache for deterministic fact answers (demo latency)."""
from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Dict, Optional, Tuple

_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_TTL_S = 600.0
_MAX = 128


def _norm(route: str, message: str, lang: str) -> str:
    m = re.sub(r"\s+", " ", (message or "").strip().lower())
    raw = f"{route}|{(lang or 'en').lower()}|{m}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def get(route: str, message: str, lang: str) -> Optional[Dict[str, Any]]:
    key = _norm(route, message, lang)
    hit = _CACHE.get(key)
    if not hit:
        return None
    ts, payload = hit
    if time.time() - ts > _TTL_S:
        _CACHE.pop(key, None)
        return None
    return payload


def put(route: str, message: str, lang: str, payload: Dict[str, Any]) -> None:
    if len(_CACHE) >= _MAX:
        # drop oldest
        oldest = min(_CACHE.items(), key=lambda kv: kv[1][0])[0]
        _CACHE.pop(oldest, None)
    _CACHE[_norm(route, message, lang)] = (time.time(), payload)
