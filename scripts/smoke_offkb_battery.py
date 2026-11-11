"""Live battery: on-topic centre asks must NOT hard-refuse; fish/science must refuse."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Tuple

API = os.environ.get("BRIGHTNEST_API", "http://47.242.157.72:8000").rstrip("/")
REFUSE_MARK = "outside what our BrightNest materials cover"

# expect_refuse: True = must refuse; False = must answer from KB (no hard refuse)
CASES: List[Tuple[str, str, bool]] = [
    # --- programme / curriculum (the original bug class) ---
    ("phonics_more", "Can you tell me more about your phonics lessons", False),
    ("phonics_what", "What is your phonics programme like?", False),
    ("phonics_age", "Do you offer phonics classes for 5 year olds?", False),
    ("phonics_fee", "How much does phonics cost?", False),
    ("phonics_schedule", "When are phonics classes?", False),
    ("lse_alias", "Tell me about Little Sound Explorers", False),
    ("mandarin", "Tell me more about your Mandarin classes", False),
    ("pinyin", "Do you teach Mandarin pinyin?", False),
    ("writing", "What writing programmes do you have?", False),
    ("chinese_arts", "Tell me about Chinese Language Arts", False),
    ("toddler_char", "Do you have toddler character recognition?", False),
    ("alludio", "What is Alludio?", False),
    ("playgroup", "Do you have a playgroup?", False),
    ("private", "Can we book a private class?", False),
    # --- ops / policies parents actually ask ---
    ("fees", "What are your tuition fees?", False),
    ("hours", "What are your opening hours?", False),
    ("location", "Where is the centre?", False),
    ("trial", "How does a trial assessment work?", False),
    ("waitlist", "What if the class is full?", False),
    ("sibling", "Do you offer a sibling discount?", False),
    ("allergy", "What's your peanut allergy policy?", False),
    ("pickup", "Who can pick up my child?", False),
    ("typhoon", "What happens in a typhoon signal 8?", False),
    ("teachers", "What are the teachers' qualifications?", False),
    ("makeup", "Can we make up a missed class?", False),
    ("online", "Do you offer online classes?", False),
    ("uniform", "Is there a dress code or uniform?", False),
    ("bilingual", "Is the classroom bilingual?", False),
    ("summer", "Do you run summer holiday courses?", False),
    ("freeze", "Can I freeze my package?", False),
    ("observe", "Can parents observe a class?", False),
    ("deposit", "Is there a registration deposit?", False),
    ("payment", "How do I pay fees?", False),
    # --- challenging but still centre-ish phrasing ---
    ("tell_more_lessons", "Can you tell me more about your lessons?", False),
    ("sounds_letters", "Do you teach letter sounds and blending?", False),
    ("english_reading", "Do you help with early English reading?", False),
    ("putonghua", "有冇普通話班？", False),
    ("fees_zh", "學費幾多？", False),
    ("hours_zh", "營業時間係幾點？", False),
    # --- must refuse (science / homework / world knowledge) ---
    ("fish", "How do fish breathe underwater?", True),
    ("evolution", "Can you explain Darwin's theory of evolution?", True),
    ("photosynthesis", "Explain photosynthesis for my child's homework", True),
    ("math_hw", "What's 347 times 89?", True),
    ("capitals", "What's the capital of France?", True),
    ("weather_world", "Will it rain in London tomorrow?", True),
]


def req(method: str, path: str, body: Dict | None = None) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(f"{API}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def chat(msg: str, sid: str) -> Dict:
    return req(
        "POST",
        "/chat",
        {
            "message": msg,
            "session_id": sid,
            "debug": True,
            "phone": "+852 9000 1111",
            "parent_name": "Battery Parent",
            "child_name": "Battery Kid",
        },
    )


def reset(sid: str) -> None:
    try:
        req("DELETE", f"/chat/history/{urllib.parse.quote(sid, safe='')}")
    except Exception:
        pass


def main() -> int:
    print("API", API)
    try:
        print("health", req("GET", "/healthz"))
    except Exception as e:
        print("health FAIL", e)
        return 2

    fails: List[str] = []
    rows: List[Dict[str, Any]] = []

    for cid, msg, expect_refuse in CASES:
        sid = f"offkb-{cid}"
        reset(sid)
        try:
            p = chat(msg, sid)
        except Exception as e:
            print(f"FAIL {cid}: request error {e}")
            fails.append(cid)
            continue
        ans = (p.get("answer") or "").strip()
        tr = p.get("trace") or {}
        dbg = (tr.get("rag") or p.get("debug") or {}) if isinstance(tr, dict) else {}
        # ChatResponse often nests rag debug under trace.rag or top-level debug
        if not dbg and isinstance(p.get("debug"), dict):
            dbg = p["debug"]
        gap = dbg.get("gap_reason") or (tr.get("rag") or {}).get("gap_reason")
        top = dbg.get("top_score")
        if top is None and isinstance(tr.get("rag"), dict):
            top = tr["rag"].get("top_score")
        route = tr.get("route")
        refused = REFUSE_MARK.lower() in ans.lower()
        # Also catch zh refuse variants
        if "唔屬於亮巢" in ans or "不属于亮巢" in ans:
            refused = True
        ok = refused if expect_refuse else (not refused)
        # Soft: on-topic should have some substance
        if ok and not expect_refuse and len(ans) < 40:
            ok = False
        status = "OK" if ok else "FAIL"
        if not ok:
            fails.append(cid)
        preview = ans.replace("\n", " ")[:140]
        row = {
            "id": cid,
            "expect_refuse": expect_refuse,
            "refused": refused,
            "ok": ok,
            "route": route,
            "gap": gap,
            "top": top,
            "preview": preview,
        }
        rows.append(row)
        print(
            f"{status} {cid} refuse={refused} gap={gap} top={top} route={route} | {preview}"
        )
        time.sleep(0.15)

    out_path = os.path.join(os.path.dirname(__file__), "smoke_offkb_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"fails": fails, "rows": rows}, f, ensure_ascii=False, indent=2)
    print(f"\n{len(rows) - len(fails)}/{len(rows)} passed; fails={fails}")
    print("wrote", out_path)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
