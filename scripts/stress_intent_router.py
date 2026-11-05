"""Ad-hoc intent-router stress probe (not a pytest suite)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from services.intent_router import decide_intent  # noqa: E402

CASES = [
    ("typhoon T8", "What happens when there is a typhoon signal 8?", "severe_weather"),
    ("T8 open?", "Are you open during T8?", "severe_weather"),
    ("black rain", "Will you close for black rain?", "severe_weather"),
    ("Christmas", "Are you open on Christmas Day?", "opening_hours"),
    ("hours", "What are your opening hours?", "opening_hours"),
    ("tomorrow hours", "What about tomorrow?", None),  # needs history
    ("phonics fee", "How much is phonics?", "fees"),
    ("private fee", "How much is private English tuition?", "fees"),
    ("how much time", "How much time does phonics take?", "rag"),
    ("private offer", "Do you offer private 1:1 tutoring?", "rag"),
    ("seats", "How many seats are left in Saturday phonics?", "booking_tool"),
    ("show slots", "Show trial class availability", "booking_tool"),
    ("book", "Can I book a class for Jason next week?", "booking_dialogue"),
    ("cancel", "Can I cancel Jason's class next Tuesday?", "cancel_dialogue"),
    ("teachers", "Who teaches Mandarin?", "teachers"),
    ("location", "Where are you located?", "location"),
    ("parking", "Is there parking near the centre?", "location"),
    ("guest", "Can I bring my friend to the trial class?", "guest_policy"),
    ("scuba", "Does BrightNest offer scuba diving for toddlers?", "unknown_programme"),
    ("tokyo", "What's the weather in Tokyo tomorrow?", "off_domain"),
    ("staff", "Can I speak to a real person please?", "staff_escalation"),
    ("angry", "This is unacceptable — I'm furious about today's class!", "staff_escalation"),
    (
        "safety",
        "My child has a peanut allergy and had a reaction after class yesterday — please call me ASAP",
        "staff_escalation",
    ),
    ("allergy faq", "Do you have a peanut allergy policy?", "allergy"),
    ("makeup", "What is your make-up class policy?", "makeup"),
    ("sibling", "Do you have a sibling discount?", "sibling"),
    ("waiting", "Is there a waiting list if the class is full?", "waiting"),
    ("payment", "How do I pay by FPS?", "payment"),
    ("pets", "Can I bring my dog to the centre?", "pets_policy"),
    ("boarding", "Do you offer overnight boarding?", "unsupported_facility"),
    ("trial info", "Do you offer trial classes?", "trial_info"),
    ("trial policy", "Does my child need a trial assessment before joining?", "trial_info"),
    (
        "multi",
        "My shy 4yo has eczema, grandparents pickup, phonics budget and parking — what do you suggest?",
        "rag",
    ),
    ("thanks", "Thanks!", "thanks"),
    ("hi", "Hi", "greeting"),
    ("confused", "Huh?", "confused"),
    # Ambiguous / edge
    ("fee+typhoon", "How much is phonics if there is a typhoon?", None),
    ("seats+fee", "How many seats are left and how much is phonics?", "booking_tool"),
    ("book trial", "I'd like to book a trial assessment for Saturday", "booking_dialogue"),
    ("need trial first?", "Do I need to book a trial first before enrolling?", "trial_info"),
    ("zh typhoon", "八號風球會唔會停課？", "severe_weather"),
    ("zh fee", "拼音學費幾多錢？", "fees"),
    ("zh monthly fee", "月費幾多？", "fees"),
    ("zh fee short", "費用幾多", "fees"),
    ("zh gei chin", "幾錢一個月", "fees"),
    ("zh class fee", "班費幾多", "fees"),
    ("zh seats", "星期六拼音班仲有冇位？", "booking_tool"),
    ("zh seats2", "拼音班有冇位？", "booking_tool"),
    ("zh seats+fee", "有冇位同月費幾多？", "booking_tool"),
    ("zh waitlist", "滿班嘅話可唔可以候補？", "waiting"),
    ("zh no seat wait", "冇位，可以候補嗎", "waiting"),
    ("en waitlist", "Can you put us on the waiting list?", "waiting"),
    ("en full", "The Saturday phonics class is full — what now?", "waiting"),
    # Negatives — must NOT steal into fees
    ("zh age not fee", "小朋友幾多歲可以入讀？", "rag"),
]


def main() -> int:
    bad = []
    for name, msg, exp in CASES:
        d = decide_intent(msg, lang="en", session_id=f"stress-{name}")
        top = sorted(d.scores.items(), key=lambda kv: -kv[1])[:4]
        top_s = ", ".join(f"{k}={v:.2f}" for k, v in top)
        ok = exp is None or d.route == exp
        mark = "OK" if ok else "FAIL"
        if not ok:
            bad.append((name, exp, d.route, dict(d.scores)))
        print(f"{mark:4} {name:16} -> {d.route:20} | {top_s}")
    print("---")
    print(f"{len(bad)} unexpected routes / {len(CASES)} cases")
    for name, exp, got, scores in bad:
        print(f"  FAIL {name}: expected {exp}, got {got}, scores={scores}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
