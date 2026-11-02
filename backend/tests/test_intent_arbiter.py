"""Intent arbiter: only review structured picks; parse JSON safely."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import intent_arbiter as ia  # noqa: E402


def test_should_review_skips_hard_routes():
    assert ia.should_review("rag") is False
    assert ia.should_review("staff_escalation") is False
    assert ia.should_review("greeting") is False


def test_should_review_skips_active_cancel(monkeypatch):
    monkeypatch.setattr(ia.SETTINGS, "intent_arbiter_enabled", True)
    monkeypatch.setattr(ia.SETTINGS, "dashscope_api_key", "x")
    assert ia.should_review("cancel_dialogue", debug={"cancel_active": True}) is False
    assert ia.should_review("teachers", debug={}) is True
    assert ia.should_review("confused", debug={}) is True
    assert ia.should_review("off_domain", debug={}) is True


def test_review_reroutes_to_rag(monkeypatch):
    monkeypatch.setattr(ia.SETTINGS, "intent_arbiter_enabled", True)
    monkeypatch.setattr(ia.SETTINGS, "dashscope_api_key", "x")
    monkeypatch.setattr(ia.SETTINGS, "intent_arbiter_timeout_s", 2.0)

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "output": {
                    "choices": [
                        {
                            "message": {
                                "content": '{"action":"rag","route":"rag","reason":"needs KB narrative"}'
                            }
                        }
                    ]
                }
            }

    with patch("services.intent_arbiter.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.post.return_value = _Resp()
        out = ia.review_intent(
            "Do you offer phonics classes for 5 year olds?",
            proposed_route="teachers",
            scores={"teachers": 0.95},
            debug={},
        )
    assert out["action"] == "rag"
    assert out["route"] == "rag"
