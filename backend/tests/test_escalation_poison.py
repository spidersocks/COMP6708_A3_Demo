"""Safety allegations like poisoning must hard-match with a visible cue."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import escalation  # noqa: E402
from services.intent_router import decide_intent  # noqa: E402


def test_poisoned_child_is_safety_cue():
    hit = escalation.detect_escalation("You poisoned my child.")
    assert hit is not None
    assert hit["category"] == "safety"
    assert "poison" in (hit.get("cue") or "").lower()


def test_poison_rule_shows_cue_not_empty_checks():
    d = decide_intent(
        "You poisoned my child.",
        lang="en",
        session_id="esc-poison-test",
    )
    assert d.route == "staff_escalation"
    assert float(d.scores.get("staff_escalation") or 0) >= 0.99
    rules = [
        r
        for r in (d.debug or {}).get("rules_evaluated") or []
        if r.get("id") == "staff_escalation"
    ]
    assert rules
    r = rules[0]
    assert r.get("matched")
    assert "poison" in (r.get("why") or "").lower()
    safety_chk = next(
        (c for c in r.get("checks") or [] if "safety" in (c.get("label") or "")),
        None,
    )
    assert safety_chk and safety_chk.get("passed")
    assert "poison" in (safety_chk.get("detail") or "").lower()
    assert (r.get("matched_span") or {}).get("text")
