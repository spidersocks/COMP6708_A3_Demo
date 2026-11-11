"""Comprehensive live battery after tools-only router cut."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

API = os.environ.get("BRIGHTNEST_API", "http://47.242.157.72:8000").rstrip("/")
REFUSE_MARK = "outside what our BrightNest materials cover"

# (id, message, expected_route or None, expect_refuse, must_contain_any)
# expected_route None = any non-refuse answer OK but prefer rag for FAQs
CASES: List[Tuple[str, str, Optional[str], bool, List[str]]] = [
    # --- tools ---
    ("typhoon", "What happens under typhoon signal 8?", "severe_weather", False, ["suspend", "8", "暫停", "停"]),
    ("hours", "What are your opening hours?", "opening_hours", False, ["09:00", "18:00", "Monday", "星期一"]),
    ("seats", "How many seats are left in Saturday phonics?", "booking_tool", False, ["seat", "slot", "位", "open"]),
    ("book", "I'd like to book a class for Jason", "booking_dialogue", False, ["Jason", "class", "Which", "programme", "phonics"]),
    ("escalate", "Can I speak to a real person please?", "staff_escalation", False, ["team", "staff", "reach", "working"]),
    ("cancel_start", "I need to cancel my booking", "cancel_dialogue", False, ["cancel", "取消", "which", "booking"]),
    # --- RAG static FAQs (must NOT refuse; route should be rag) ---
    ("phonics", "Can you tell me more about your phonics lessons", "rag", False, ["phonic", "sound", "letter"]),
    ("fees", "What are your tuition fees?", "rag", False, ["HK$", "3,200", "fee", "package"]),
    ("fees_zh", "月費幾多？", "rag", False, ["HK$", "學費", "2,", "3,"]),
    ("payment", "How do I pay? FPS details?", "rag", False, ["FPS", "Chong Hing", "256-20"]),
    ("payment_zh", "點樣俾學費？有冇轉數快？", "rag", False, ["FPS", "轉數快", "創興", "Chong", "256"]),
    ("location", "Where is the centre?", "rag", False, ["Queen", "Harbourview", "Central", "D2"]),
    ("location_zh", "你哋喺邊？點搭港鐵？", "rag", False, ["皇后", "中環", "D2", "海景"]),
    ("teachers", "Tell me about your teachers", "rag", False, ["Avery", "Wei", "teacher", "CELTA", "bilingual"]),
    ("pickup", "Who can pick up my child?", "rag", False, ["pickup", "authoris", "helper", "grandparent", "collect"]),
    ("allergy", "What's your peanut allergy policy?", "rag", False, ["nut", "peanut", "allerg"]),
    ("makeup", "What is your make-up class policy?", "rag", False, ["make", "quota", "補", "2", "3"]),
    ("sibling", "Do you have a sibling discount?", "rag", False, ["8%", "sibling", "discount"]),
    ("waitlist", "Is there a waiting list if a class is full?", "rag", False, ["wait", "1–3", "1-3", "full"]),
    ("pets", "Can my dog sit in on the trial?", "rag", False, ["pet", "dog", "cannot", "not", "寵物"]),
    ("trial", "Do you offer a trial assessment?", "rag", False, ["45", "180", "trial", "assessment"]),
    ("mandarin", "Tell me more about your Mandarin classes", "rag", False, ["Mandarin", "Pinyin", "普通話", "Putonghua"]),
    ("illness", "Fever of 39C — should we come to class?", "rag", False, ["38", "fever", "home", "發燒"]),
    # --- refuse ---
    ("fish", "How do fish breathe underwater?", None, True, []),
    ("evolution", "Explain Darwin's theory of evolution", None, True, []),
    ("tokyo", "What's the weather in Tokyo tomorrow?", "off_domain", True, []),
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
            "phone": "+852 9000 2222",
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
    print("health", req("GET", "/healthz"))
    fails: List[str] = []
    rows: List[Dict[str, Any]] = []

    for cid, msg, expect_route, expect_refuse, needles in CASES:
        sid = f"toolsrag-{cid}"
        reset(sid)
        try:
            p = chat(msg, sid)
        except Exception as e:
            print(f"FAIL {cid}: {e}")
            fails.append(cid)
            continue
        ans = (p.get("answer") or "").strip()
        tr = p.get("trace") or {}
        route = tr.get("route")
        # opening hours may report as deterministic_opening_hours
        route_norm = route
        if route == "deterministic_opening_hours":
            route_norm = "opening_hours"
        refused = REFUSE_MARK.lower() in ans.lower()
        if "唔屬於亮巢" in ans or "不属于亮巢" in ans:
            refused = True
        # off_domain refuse wording
        low_ans = ans.lower().replace("’", "'").replace("‘", "'")
        if (
            "outside what we can help" in low_ans
            or "don't have weather" in low_ans
            or "other cities" in low_ans
        ):
            refused = True
        if expect_route == "off_domain" and route_norm == "off_domain":
            refused = True  # hard off-domain path is the refuse

        ok = True
        reasons: List[str] = []
        if expect_refuse and not refused:
            ok = False
            reasons.append("expected refuse")
        if not expect_refuse and refused:
            ok = False
            reasons.append("false refuse")
        if expect_route and route_norm != expect_route:
            # Allow rag when arbiter/soft path still answers well for FAQs
            if expect_route == "rag" and route_norm not in {
                "rag",
                "deterministic_opening_hours",
            }:
                ok = False
                reasons.append(f"route={route} want={expect_route}")
            elif expect_route != "rag":
                ok = False
                reasons.append(f"route={route} want={expect_route}")
        if not expect_refuse and needles:
            low = ans.lower()
            if not any(n.lower() in low for n in needles):
                ok = False
                reasons.append("missing content cues")
        if not expect_refuse and len(ans) < 40:
            ok = False
            reasons.append("too short")

        status = "OK" if ok else "FAIL"
        if not ok:
            fails.append(cid)
        preview = ans.replace("\n", " ")[:130]
        row = {
            "id": cid,
            "ok": ok,
            "route": route,
            "refused": refused,
            "reasons": reasons,
            "preview": preview,
        }
        rows.append(row)
        print(f"{status} {cid} route={route} refuse={refused} {reasons} | {preview}")
        time.sleep(0.12)

    out_path = os.path.join(os.path.dirname(__file__), "smoke_tools_rag_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"fails": fails, "rows": rows}, f, ensure_ascii=False, indent=2)
    print(f"\n{len(rows) - len(fails)}/{len(rows)} passed; fails={fails}")
    print("wrote", out_path)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
