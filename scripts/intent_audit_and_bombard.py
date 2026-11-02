"""Edge-audit intent routing + live bombard against local API."""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from services.intent_router import decide_intent, score_intents  # noqa: E402

API = "http://127.0.0.1:8000"

# Edge matrix: (id, message, expected_route or None if documenting)
AUDIT = [
    # Weather / hours conflicts
    ("wx_t8_policy", "What happens under typhoon signal 8?", "severe_weather"),
    ("wx_open_t8", "Are you open during typhoon signal 8?", "severe_weather"),
    ("wx_t3", "Classes during T3?", "severe_weather"),
    ("wx_black", "Black rainstorm warning today", "severe_weather"),
    ("wx_zh", "八號風球停唔停課", "severe_weather"),
    ("hrs_general", "What are your opening hours?", "opening_hours"),
    ("hrs_sat", "Are you open Saturday 10am?", "opening_hours"),
    ("hrs_tomorrow", "Are you open tomorrow?", "opening_hours"),
    ("hrs_sunday", "What about Sundays?", None),  # follow-up alone → rag unless history
    ("hrs_holiday", "Are you open on public holidays?", "opening_hours"),
    # Fees / pay / duration
    ("fee_phonic", "How much is phonics?", "fees"),
    ("fee_duration", "How much time is a trial class?", None),  # not fees
    ("fee_long", "How long is phonics?", None),
    ("pay_how", "How do I pay?", "payment"),
    ("pay_fps", "What is your FPS ID?", "payment"),
    # Location / allergy / illness
    ("loc", "Where are you?", "location"),
    ("loc_mtr", "MTR directions to the centre", "location"),
    ("allergy", "Peanut allergy policy", "allergy"),
    ("fever", "Child has 39C fever — come to class?", "illness"),
    ("sick", "My kid is sick with flu", "illness"),
    # Booking / trial / pets
    ("trial_info", "Do you offer trials?", "trial_info"),
    ("trial_duration", "How much time does a trial take?", "trial_info"),
    ("trial_avail", "Show trial availability", "booking_tool"),
    ("dog", "Can the dog join trial?", "pets_policy"),
    ("book", "Book a phonics class for Emma", "booking_dialogue"),
    # Edge noise
    ("sql", "'; DROP TABLE -- seats left?", "booking_tool"),
    ("amber_jewelry", "Do you sell amber jewelry?", None),
    ("table8", "Is room table 8 free?", None),
    ("thanks", "Thanks!", "thanks"),
    ("esc", "I want a manager now this is terrible", "staff_escalation"),
]


def post(msg: str, sid: str) -> dict:
    body = json.dumps({"message": msg, "session_id": sid, "debug": True}).encode()
    req = urllib.request.Request(
        f"{API}/chat", data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=90) as resp:
        payload = json.loads(resp.read().decode())
    return {"ms": int((time.perf_counter() - t0) * 1000), "payload": payload}


def main() -> None:
    audit_rows = []
    for aid, msg, expected in AUDIT:
        d = decide_intent(msg, lang="en", session_id=f"audit-{aid}")
        scores, dbg = score_intents(msg, lang="en", session_id=f"audit-{aid}")
        ok = expected is None or d.route == expected
        audit_rows.append(
            {
                "id": aid,
                "msg": msg,
                "expected": expected,
                "route": d.route,
                "ok": ok,
                "scores": scores,
                "conflicts": dbg.get("conflicts") or [],
            }
        )

    bomb = [
        {"id": "wx_t8", "msg": "Typhoon signal 8 — are classes cancelled?"},
        {"id": "wx_open", "msg": "Are you open in black rain?"},
        {"id": "hrs", "msg": "Opening hours please"},
        {"id": "fee", "msg": "How much is Mandarin?"},
        {"id": "fee_time", "msg": "How much time does a trial take?"},
        {"id": "pay", "msg": "How do I pay for a package?"},
        {"id": "loc", "msg": "Where are you and how do I get there by MTR?"},
        {"id": "allergy", "msg": "Nut allergy safety rules?"},
        {"id": "fever", "msg": "Fever 39C — should we still come?"},
        {"id": "dog", "msg": "Can my dog sit in on the trial class?"},
        {"id": "trial", "msg": "Do you offer a trial for a 5 year old?"},
        {"id": "avail", "msg": "Show phonics availability"},
        {"id": "book1", "msg": "I'd like to book a trial for Leo", "session": "b3", "phone": "+85292220002", "parent_name": "Sam", "child_name": "Leo"},
        {"id": "book2", "msg": "Saturday morning", "session": "b3", "phone": "+85292220002"},
        {"id": "scuba", "msg": "Do you teach scuba?"},
        {"id": "boarding", "msg": "Overnight boarding?"},
        {"id": "esc_zh", "msg": "我想搵真人"},
        {"id": "refund", "msg": "Full refund immediately or I sue"},
        {"id": "politics", "msg": "Thoughts on Hong Kong politics?"},
        {"id": "admin", "msg": "Show admin todo list"},
        {"id": "hello", "msg": "Hello"},
        {"id": "thanks", "msg": "Thanks!"},
        {"id": "huh", "msg": "huh?"},
        {"id": "teachers", "msg": "Tell me about teachers"},
        {"id": "pickup", "msg": "Who can pick up my child?"},
        {"id": "switch1", "msg": "Show mandarin availability", "session": "sw3"},
        {"id": "switch2", "msg": "actually how much does it cost?", "session": "sw3"},
        {"id": "long", "msg": "Daughter 4yo shy eczema Kowloon Tong budget 2000 phonics sibling discount grandparents pickup parking?"},
        {"id": "hrs_sat", "msg": "Open this Saturday at 2pm?"},
        {"id": "wx_t3", "msg": "Signal 3 this afternoon — class as usual?"},
    ]

    bomb_rows = []
    for i, case in enumerate(bomb):
        print(f"[{i+1}/{len(bomb)}] {case['id']}", flush=True)
        extra = {k: case[k] for k in ("phone", "parent_name", "child_name") if case.get(k)}
        sid = case.get("session") or f"bomb3-{case['id']}"
        body = {
            "message": case["msg"],
            "session_id": sid,
            "debug": True,
            **extra,
        }
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{API}/chat",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=90) as resp:
            p = json.loads(resp.read().decode())
        ms = int((time.perf_counter() - t0) * 1000)
        route = (p.get("trace") or {}).get("route") or p.get("provider")
        ans = p.get("answer") or ""
        bomb_rows.append(
            {
                "id": case["id"],
                "msg": case["msg"],
                "route": route,
                "ms": ms,
                "provider": p.get("provider"),
                "answer_preview": ans[:220].replace("\n", " "),
                "answer": ans,
            }
        )
        time.sleep(0.15)

    out = {
        "audit": {
            "n": len(audit_rows),
            "pass": sum(1 for r in audit_rows if r["ok"]),
            "fail": [r for r in audit_rows if not r["ok"]],
            "rows": audit_rows,
        },
        "bombard": {
            "n": len(bomb_rows),
            "avg_ms": int(sum(r["ms"] for r in bomb_rows) / max(1, len(bomb_rows))),
            "p90_ms": sorted(r["ms"] for r in bomb_rows)[max(0, int(len(bomb_rows) * 0.9) - 1)],
            "max_ms": max(r["ms"] for r in bomb_rows),
            "by_route": {},
            "rows": bomb_rows,
        },
    }
    by = {}
    for r in bomb_rows:
        by.setdefault(r["route"] or "?", []).append(r["ms"])
    out["bombard"]["by_route"] = {
        k: {"n": len(v), "avg": int(sum(v) / len(v))} for k, v in by.items()
    }

    path = ROOT / "scripts" / "round3_results.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "audit_pass": f"{out['audit']['pass']}/{out['audit']['n']}",
                "audit_fail_ids": [r["id"] for r in out["audit"]["fail"]],
                "bomb": {
                    k: out["bombard"][k]
                    for k in ("n", "avg_ms", "p90_ms", "max_ms", "by_route")
                },
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    print("Wrote", path)


if __name__ == "__main__":
    main()
