"""Multi-turn live sweep: route, latency, fake commits, calendar + families.

Oracle checks live in this script only. The product does not grow phrase lists.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

API = os.environ.get("BN_API", "http://47.242.157.72:8000").rstrip("/")

FAKE_BOOKED = re.compile(
    r"(we(?:'ve| have) booked|i(?:'ve| have) booked|is on the register|booking ref|"
    r"have reserved|已(?:經)?預約|已帮你预约|已经预约)",
    re.I,
)
PROMISE = re.compile(
    r"(reach out shortly|our team will|we will arrange|we'll arrange|staff will call)",
    re.I,
)

BOOKISH = [
    "Hello I want to try out phonics for my child",
    "Any chance of a taster session for writing?",
    "I'd like to get my son started on phonics",
    "We should arrange a placement for my 4 year old",
    "Can we come and see if mandarin suits my daughter?",
    "Sign us up for a holiday phonics brush-up",
    "Put my kid down for a maths class",
    "I need a trial assessment this week",
    "Could you fit us in for playgroup?",
    "We're hoping to start phonics soon",
    "My 4 year old should try your english sounds class",
    "Is it possible to join writing this month?",
    "I'd love to enrol my daughter in mandarin",
    "Please hold a phonics seat for us",
    "Can you get us into a class this week?",
    "Looking to begin playgroup for my toddler",
    "We want a placement test before phonics",
    "Help me start my child on early math",
    "我想幫小朋友試下拼音",
    "想預約試堂評估",
    "想報寫作班",
    "可唔可以幫我哋留個拼音位",
    "想安排普通話試堂",
    "帮我给孩子约个数学班",
    "想给孩子体验一下拼音",
    "有没有办法这周开始游戏班",
    "Please register my child for phonics",
    "Book a trial for my kid",
    "I want to reserve Saturday phonics",
    "Get us on the list for mandarin",
    "We'd like to come for an assessment",
    "Can my child sit in on a phonics class?",
    "Arrange a first class for my son",
    "I want him to start sounds class",
    "Find us a writing spot next week",
    "Need to lock in playgroup",
    "Hoping for a phonics intake",
    "Set up a trial for Terry",
    "Let's get Mia into math",
    "Start us on the phonics programme",
]

FAQISH = [
    "How much is the phonics class?",
    "What are your tuition fees?",
    "Tell me about your teachers",
    "Do I need a trial before joining?",
    "How long is a phonics lesson?",
    "Where are you located?",
    "How do I get there by MTR?",
    "My child has a peanut allergy",
    "What is your make-up class policy?",
    "Do you have a sibling discount?",
    "What ages do you take for phonics?",
    "拼音班幾耐？",
    "學費點計？",
    "老師資歷係點？",
    "試堂係咪必須先上堂？",
    "中心喺邊？",
    "What should we bring to class?",
    "Is there a waiting list?",
    "Do you teach on public holidays?",
    "How many levels of phonics are there?",
]

HOURS = [
    "What are your opening hours?",
    "Are you open this Saturday?",
    "Are you open tomorrow morning?",
    "週末营业吗？",
    "星期六開唔開門？",
]

WEATHER = [
    "What happens when there is a typhoon signal 8?",
    "Black rain — classes on?",
    "八號風球會唔會停課？",
]

CANCEL = [
    "Please cancel my child's class",
    "I need to drop Saturday phonics",
    "Can I take Jason off Thursday?",
    "我想取消堂",
]

ESCALATE = [
    "Can I speak to a real person?",
    "This is unacceptable get me a manager",
    "Please call me ASAP my child had a reaction",
]


def _post(path: str, payload: Dict[str, Any], timeout: float = 90) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(
        API + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get(path: str, timeout: float = 30) -> Any:
    req = urlrequest.Request(API + path, method="GET")
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def chat(session_id: str, message: str, phone: str, parent: str = "", child: str = "") -> Dict[str, Any]:
    t0 = time.perf_counter()
    err = ""
    body: Dict[str, Any] = {}
    try:
        body = _post(
            "/chat",
            {
                "message": message,
                "session_id": session_id,
                "phone": phone,
                "parent_name": parent,
                "child_name": child,
                "debug": True,
            },
        )
    except (HTTPError, URLError, TimeoutError) as e:
        err = str(e)
    ms = int((time.perf_counter() - t0) * 1000)
    trace = body.get("trace") or {}
    booking = trace.get("booking") or {}
    booked = (booking.get("booking") or {}) if isinstance(booking, dict) else {}
    answer = body.get("answer") or ""
    route = trace.get("route") or ""
    issues: List[str] = []
    if err:
        issues.append("http_error")
    if ms > 20000:
        issues.append("slow")
    if FAKE_BOOKED.search(answer) and not (booked.get("id") or booking.get("stage") == "booked"):
        issues.append("fake_booking")
    if PROMISE.search(answer) and not trace.get("admin_todo") and route not in {
        "booking_dialogue",
        "staff_escalation",
    }:
        issues.append("promise_without_artifact")
    policy = ((trace.get("routing") or {}).get("debug") or {}).get("action_policy") or {}
    return {
        "message": message,
        "ms": ms,
        "route": route,
        "provider": body.get("provider"),
        "stage": booking.get("stage") if isinstance(booking, dict) else None,
        "booking_id": booked.get("id"),
        "answer": answer[:400],
        "policy_act": policy.get("act"),
        "policy_conf": policy.get("confidence"),
        "admin_todo": bool(trace.get("admin_todo")),
        "issues": issues,
        "error": err,
    }


def scenario(name: str, phone: str, parent: str, child: str, turns: List[str]) -> Dict[str, Any]:
    sid = "sweep-" + uuid.uuid4().hex[:10]
    rows = []
    for msg in turns:
        rows.append(chat(sid, msg, phone, parent, child))
    return {"name": name, "session": sid, "phone": phone, "turns": rows}


def single(label: str, message: str, phone: str, parent: str, child: str, expect_routes: Optional[List[str]] = None) -> Dict[str, Any]:
    row = scenario(label, phone, parent, child, [message])
    turn = row["turns"][0]
    if expect_routes and turn["route"] not in expect_routes:
        turn["issues"] = list(turn["issues"]) + [f"route:{turn['route']}"]
    row["expect"] = expect_routes
    return row


def main() -> None:
    out_path = os.environ.get(
        "BN_SWEEP_OUT",
        str(Path(__file__).resolve().parent / "flow_sweep_results.jsonl"),
    )
    results: List[Dict[str, Any]] = []

    def record(row: Dict[str, Any]) -> None:
        results.append(row)
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        bad = [t for t in row["turns"] if t["issues"]]
        mark = "FAIL" if bad else "ok  "
        last = row["turns"][-1]
        print(
            f"{mark} {row['name'][:42]:42} route={last.get('route')} "
            f"{last.get('ms')}ms issues={last.get('issues')}",
            flush=True,
        )

    # Wipe the file
    open(out_path, "w", encoding="utf-8").close()

    new_phone = "+852 9000 4242"
    known_phone = "+852 9000 0000"

    # --- single-turn matrix (new + known) ---
    n = 0
    for msg in BOOKISH:
        n += 1
        record(
            single(
                f"new-book-{n}",
                msg,
                f"+852 9100 {n:04d}",
                "",
                "",
                ["booking_dialogue", "booking_tool"],
            )
        )
    for msg in FAQISH:
        n += 1
        record(
            single(
                f"new-faq-{n}",
                msg,
                "+852 9000 9999",
                "",
                "",
                ["rag"],
            )
        )
    for msg in HOURS:
        record(
            single(
                "hours",
                msg,
                known_phone,
                "Demo Parent",
                "Jason Chan",
                ["opening_hours", "deterministic_opening_hours"],
            )
        )
    for msg in WEATHER:
        record(
            single("weather", msg, known_phone, "Demo Parent", "Jason Chan", ["severe_weather"])
        )
    for msg in CANCEL:
        record(
            single(
                "cancel",
                msg,
                known_phone,
                "Demo Parent",
                "Jason Chan",
                ["cancel_dialogue"],
            )
        )
    for msg in ESCALATE:
        record(
            single(
                "escalate",
                msg,
                known_phone,
                "Demo Parent",
                "Jason Chan",
                ["staff_escalation"],
            )
        )

    # Known parent book paraphrases (should not ask for identity)
    for i, msg in enumerate(BOOKISH[:15], start=1):
        record(
            single(
                f"known-book-{i}",
                msg,
                known_phone,
                "Demo Parent",
                "Jason Chan",
                ["booking_dialogue", "booking_tool"],
            )
        )

    # --- multi-turn flows ---
    flows = [
        (
            "new-phonics-tryout",
            new_phone,
            "",
            "",
            [
                "Hello I want to try out phonics for my child",
                "I'm David Gunton. My child is Terry Gunton. Yes, phonics",
                "This week",
                "Thursday",
                "yes",
            ],
        ),
        (
            "new-math-plain",
            "+852 9000 4343",
            "",
            "",
            [
                "I want to book a math class",
                "I'm Pat Lee and my child is Bo Lee",
                "Next week",
                "Tuesday",
                "yes",
            ],
        ),
        (
            "known-phonics-extra",
            known_phone,
            "Demo Parent",
            "Jason Chan",
            [
                "Book another phonics class for Jason",
                "This week",
                "Saturday",
                "yes",
            ],
        ),
        (
            "known-fees-then-book",
            known_phone,
            "Demo Parent",
            "Jason Chan",
            [
                "How much is phonics?",
                "Ok let's book one this week",
                "Friday",
                "yes",
            ],
        ),
        (
            "new-hours-then-try",
            "+852 9000 4545",
            "",
            "",
            [
                "Are you open on Saturday?",
                "Great, I want to try writing for my child",
                "I'm Sam Ho. Child is Nina Ho.",
                "This week",
                "Saturday",
            ],
        ),
        (
            "known-cancel",
            known_phone,
            "Demo Parent",
            "Jason Chan",
            [
                "Please cancel one of Jason's phonics classes",
                "the first one",
                "yes",
            ],
        ),
        (
            "new-mandarin-awkward",
            "+852 9000 4646",
            "",
            "",
            [
                "My daughter ought to start your chinese speaking class",
                "Parent is Alex Wong, child is Mia Wong",
                "Any time next week",
            ],
        ),
        (
            "faq-allergy",
            "+852 9000 9999",
            "",
            "",
            ["My child has a peanut allergy — what do you do?"],
        ),
        (
            "off-topic",
            "+852 9000 9999",
            "",
            "",
            ["Can you help my child with a science homework question about volcanoes?"],
        ),
        (
            "weather-then-hours",
            known_phone,
            "Demo Parent",
            "Jason Chan",
            [
                "Typhoon signal 8 tonight — is class on?",
                "What about your normal Saturday hours?",
            ],
        ),
    ]
    for name, phone, parent, child, turns in flows:
        record(scenario(name, phone, parent, child, turns))

    # Side effect: last successful booking should show on schedule + families
    side = {"name": "side_effects", "turns": [], "notes": []}
    try:
        sched = _get("/schedule?days=14&lang=en")
        blob = json.dumps(sched)
        side["notes"].append("terry_on_calendar" if "Terry Gunton" in blob or "terry gunton" in blob.lower() else "terry_missing")
        side["notes"].append("schedule_ok")
    except Exception as e:
        side["notes"].append(f"schedule_err:{e}")
    try:
        fams = _get("/admin/families")
        blob = json.dumps(fams)
        side["notes"].append("terry_family" if "Terry" in blob else "terry_family_missing")
    except Exception as e:
        side["notes"].append(f"families_err:{e}")
    side["turns"] = [{"message": "side", "ms": 0, "route": "", "issues": [n for n in side["notes"] if "missing" in n or "err" in n], "answer": " ".join(side["notes"])}]
    record(side)

    issues = []
    turns_n = 0
    slow = 0
    for row in results:
        for t in row["turns"]:
            turns_n += 1
            if t.get("ms", 0) > 8000:
                slow += 1
            for iss in t.get("issues") or []:
                issues.append((row["name"], iss, t.get("route"), t.get("message", "")[:60]))
    summary = {
        "name": "_summary",
        "turns": [{"message": "summary", "ms": 0, "route": "", "issues": [], "answer": ""}],
        "turn_count": turns_n,
        "issue_count": len(issues),
        "slow_over_8s": slow,
        "issues": issues[:80],
    }
    record(summary)
    print(f"\nTURNS {turns_n} ISSUES {len(issues)} SLOW {slow} -> {out_path}", flush=True)


if __name__ == "__main__":
    main()
