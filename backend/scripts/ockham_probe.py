"""Post-Ockham intent probe — run from backend/ with PYTHONPATH=."""
from __future__ import annotations

from services.intent_router import decide_intent, score_intents

CASES = [
    ("What are your opening hours?", "opening_hours"),
    ("Are you open tomorrow?", "opening_hours"),
    ("What happens when there is a typhoon signal 8?", "severe_weather"),
    ("Are you open during typhoon signal 8?", "severe_weather"),
    ("Black rain — classes on?", "severe_weather"),
    ("How much is the phonics class?", "rag"),
    ("Where are you located?", "rag"),
    ("Tell me about your teachers", "rag"),
    ("My child has a peanut allergy", "rag"),
    ("Show trial class availability", "booking_tool"),
    ("How many seats are left in Saturday phonics?", "booking_tool"),
    ("I want to book a trial class", "booking_dialogue"),
    ("Please cancel my booking", "cancel_dialogue"),
    ("Can I speak to a real person?", "staff_escalation"),
    ("What's the weather in Tokyo tomorrow?", "rag"),
    ("Hello", "rag"),
    ("Do you offer private 1:1 tutoring?", "rag"),
    ("FPS payment details?", "rag"),
    ("八號風球會唔會停課？", "severe_weather"),
    ("星期六拼音班仲有冇位？", "booking_tool"),
    ("月費幾多？", "rag"),
    ("Can I book a slot for Saturday?", "booking_dialogue"),
]


def main() -> int:
    fail = 0
    for msg, exp in CASES:
        d = decide_intent(msg, lang="en", session_id="ockham-probe")
        arb = (d.debug or {}).get("arbiter") or {}
        if d.route != exp:
            fail += 1
            print("FAIL", repr(msg), "got", d.route, "want", exp, "scores", d.scores)
        else:
            print(
                "OK  ",
                d.route,
                "| arbiter",
                arb.get("reason"),
                "called=",
                arb.get("called"),
                "|",
                msg[:48],
            )

    scores, dbg = score_intents("Are you open during T8?", lang="en", session_id="t")
    assert "severe_weather" in scores and "opening_hours" not in scores, scores
    assert not (dbg.get("conflicts") or []), dbg.get("conflicts")
    print("OK   weather owns T8/open phrase (no hours award, no conflict)", scores)

    scores_bad, _ = score_intents(
        "Are you open in bad weather?", lang="en", session_id="t"
    )
    assert "severe_weather" in scores_bad and "opening_hours" not in scores_bad, scores_bad
    print("OK   bad-weather open? → severe_weather only", scores_bad)

    scores_c, _ = score_intents(
        "Please cancel my booking", lang="en", session_id="t"
    )
    assert "cancel_dialogue" in scores_c
    assert "booking_dialogue" not in scores_c, scores_c
    print("OK   cancel does not also award booking", scores_c)

    for msg in [
        "What are your opening hours?",
        "Show trial class availability",
        "Please cancel my booking",
        "Can I speak to a real person?",
        "Are you open during typhoon signal 8?",
    ]:
        d = decide_intent(msg, lang="en", session_id="ockham-probe2")
        arb = d.debug["arbiter"]
        assert arb.get("called") is False, (msg, arb)
        assert arb.get("reason") == "clear_pick_skip", (msg, arb)
    print("OK   arbiter skipped on clear detector wins")

    d = decide_intent("How much is phonics?", lang="en", session_id="ockham-probe3")
    assert d.route == "rag"
    assert d.debug["arbiter"].get("called") is False
    print("OK   arbiter skipped on RAG default")

    from services import final_check

    gate = final_check.affirmative_proceed(kind="book", proposed_summary="x")
    assert gate["action"] == "proceed" and gate["called"] is False
    safety = final_check.review_commit(
        kind="escalate",
        message="hurt my child",
        proposed_summary="safety handoff",
        context={"force_proceed": True, "category": "safety"},
    )
    assert safety["action"] == "proceed" and safety["called"] is False
    assert safety["reason"] == "force_proceed"
    print("OK   final_check affirmative + force_proceed skip LLM")

    print("PROBE_FAILS", fail)
    return fail


if __name__ == "__main__":
    raise SystemExit(main())
