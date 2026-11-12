"""Live battery: tool correctness + data_sources provenance for the inspector."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

API = os.environ.get("BRIGHTNEST_API", "https://seanfontaine.dev/brightnest-api").rstrip("/")


class _PostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if code in (301, 302, 303, 307, 308) and req.get_method() == "POST":
            return urllib.request.Request(
                newurl,
                data=req.data,
                headers={k: v for k, v in req.header_items()},
                method="POST",
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_PostRedirect)

# id, message, expect_routes, must_source_ids (any), forbid_refuse
CASES: List[Tuple[str, str, List[str], List[str], bool]] = [
    ("typhoon", "What happens under typhoon signal 8?", ["severe_weather"], ["centre_weather_json", "hko_opendata"], False),
    ("hours", "What are your opening hours?", ["opening_hours", "deterministic_opening_hours"], ["hours_calendar"], False),
    ("open_now", "Are you open right now?", ["opening_hours", "deterministic_opening_hours"], ["hours_calendar", "hko_opendata"], False),
    ("seats", "How many seats are left in Saturday phonics?", ["booking_tool"], ["class_register_csv"], False),
    ("fees_en", "How much is phonics?", ["rag"], ["chroma_kb"], False),
    ("fees_zh", "月費幾多？", ["rag"], ["chroma_kb"], False),
    ("fees_zh2", "拼音班幾錢？", ["rag"], ["chroma_kb"], False),
    ("book", "I'd like to book a class for Jason", ["booking_dialogue"], ["bookings_sqlite", "families_csv"], False),
    ("esc", "My child had an allergic reaction — call me ASAP", ["staff_escalation"], ["staff_messages"], False),
    ("tokyo", "What's the weather in Tokyo tomorrow?", ["rag", "off_domain"], [], True),
]


def req(method: str, path: str, body: Optional[Dict] = None) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(f"{API}{path}", data=data, headers=headers, method=method)
    with _OPENER.open(r, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def chat(msg: str, sid: str) -> Dict:
    return req(
        "POST",
        "/chat",
        {
            "message": msg,
            "session_id": sid,
            "debug": True,
            "phone": "+852 9000 0000",
            "parent_name": "Demo Parent",
            "child_name": "Jason Chan",
        },
    )


def main() -> int:
    print("API", API)
    print("health", req("GET", "/healthz"))
    fails: List[str] = []
    rows: List[Dict[str, Any]] = []
    refuse_mark = "outside what our BrightNest materials cover"

    for cid, msg, routes, need_src, expect_refuse in CASES:
        sid = f"clarity-{cid}-{int(time.time()) % 100000}"
        try:
            p = chat(msg, sid)
        except Exception as e:
            print(f"FAIL {cid}: {e}")
            fails.append(cid)
            continue
        tr = p.get("trace") or {}
        route = tr.get("route") or ""
        route_n = "opening_hours" if route == "deterministic_opening_hours" else route
        tools = tr.get("tools") or {}
        ds = tr.get("data_sources") or {}
        consulted = {c.get("id") for c in (ds.get("consulted") or []) if c.get("id")}
        ans = (p.get("answer") or "").strip()
        refused = refuse_mark.lower() in ans.lower()

        reasons: List[str] = []
        ok = True
        if route_n not in routes and route not in routes:
            ok = False
            reasons.append(f"route={route}")
        if expect_refuse and not refused:
            ok = False
            reasons.append("expected refuse")
        if not expect_refuse and refused:
            ok = False
            reasons.append("false refuse")
        if need_src and not (set(need_src) & consulted):
            ok = False
            reasons.append(f"missing sources want={need_src} got={sorted(consulted)}")
        if not tools.get("selected") and route:
            # older deploys; soft
            pass
        if not ok:
            fails.append(cid)
        row = {
            "id": cid,
            "ok": ok,
            "route": route,
            "tool": tools.get("selected"),
            "selector": tools.get("selector"),
            "sources": sorted(consulted),
            "reasons": reasons,
            "preview": ans.replace("\n", " ")[:110],
        }
        rows.append(row)
        print(
            f"{'OK' if ok else 'FAIL'} {cid} route={route} tool={tools.get('selected')} "
            f"sel={tools.get('selector')} src={sorted(consulted)} {reasons}"
        )
        time.sleep(0.15)

    out = os.path.join(os.path.dirname(__file__), "inspector_clarity_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"fails": fails, "rows": rows}, f, ensure_ascii=False, indent=2)
    print(f"\n{len(rows) - len(fails)}/{len(rows)} passed; fails={fails}")
    print("wrote", out)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
