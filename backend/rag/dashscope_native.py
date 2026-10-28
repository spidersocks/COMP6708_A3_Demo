"""Configure the official DashScope SDK for native /api/v1 calls."""
from __future__ import annotations

import dashscope

from config import SETTINGS


def native_api_base() -> str:
    """Return normalized native DashScope root: https://.../api/v1"""
    base = (SETTINGS.dashscope_base_url or "").strip().rstrip("/")
    if not base:
        return "https://dashscope.aliyuncs.com/api/v1"
    if base.endswith("/compatible-mode/v1"):
        base = base[: -len("/compatible-mode/v1")] + "/api/v1"
    elif not base.endswith("/api/v1"):
        if "/api/" not in base and "/compatible-mode/" not in base:
            base = f"{base}/api/v1"
    return base


def configure_dashscope() -> None:
    """Point the SDK at the native Model Studio / DashScope HTTP API."""
    if not SETTINGS.dashscope_api_key:
        return
    dashscope.api_key = SETTINGS.dashscope_api_key.strip()
    dashscope.base_http_api_url = native_api_base()


def auth_headers() -> dict:
    return {
        "Authorization": f"Bearer {SETTINGS.dashscope_api_key.strip()}",
        "Content-Type": "application/json",
    }
