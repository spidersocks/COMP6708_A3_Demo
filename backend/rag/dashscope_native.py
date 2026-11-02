"""Configure the official DashScope SDK for native /api/v1 calls."""
from __future__ import annotations

from typing import Any, Dict, Optional

import dashscope
import httpx

from config import SETTINGS

# Reuse TLS connections to the MaaS endpoint (new Client per call was costly).
_http: Optional[httpx.Client] = None


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


def http_client(timeout: float = 90.0) -> httpx.Client:
    """Process-wide keep-alive client for DashScope / MaaS HTTP."""
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=8, max_connections=16),
            http2=False,
        )
    return _http


def generation_parameters(
    *,
    max_tokens: int,
    temperature: float = 0.3,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Native generation params.

    Critical: qwen3.8-flash enables *thinking* by default (reasoning_effort=xhigh).
    Non-streaming demo calls must set enable_thinking=false or each reply burns
    tens of thousands of CoT tokens and multiplies latency.
    """
    params: Dict[str, Any] = {
        "result_format": "message",
        "temperature": temperature,
        "max_tokens": max_tokens,
        "enable_thinking": False,
    }
    if extra:
        params.update(extra)
    return params
