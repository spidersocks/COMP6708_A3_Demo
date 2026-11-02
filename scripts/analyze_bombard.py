import json
from collections import Counter

with open("scripts/bombard_results.json", encoding="utf-8") as f:
    data = json.load(f)
rows = data["rows"]

print("=== FLAGGED / NON-OK ===")
for r in rows:
    if r.get("grade") in ("weak", "bad", "fail") or r.get("flags"):
        print(f"{r['grade']:4} {r['id']:20} flags={r.get('flags')} route={r.get('route')} ms={r.get('ms')}")
        print(f"     Q: {r['msg'][:120]}")
        print(f"     A: {(r.get('answer_preview') or r.get('error') or '')[:240]}")
        print()

print("=== ROUTES ===", Counter(r.get("route") for r in rows))
print("=== PROVIDERS ===", Counter(r.get("provider") for r in rows))
ms = sorted(r["ms"] for r in rows)
print("MS p50/p90/max/avg", ms[len(ms) // 2], ms[int(len(ms) * 0.9)], max(ms), data["summary"]["avg_ms"])

# Manual quality notes: scan for issues
issues = []
for r in rows:
    ans = (r.get("answer") or "").lower()
    msg = r["msg"].lower()
    note = []
    if "little scholars" in ans and r["id"] != "edge_competitor":
        note.append("mentions_real_brand")
    if r["cat"] == "gap":
        if "scuba" in msg and ("offer scuba" in ans or "scuba diving" in ans) and "don't" not in ans and "do not" not in ans and "not offer" not in ans and "don't offer" not in ans and "we don't" not in ans:
            # check negation nearby
            if "not" not in ans[:200] and "don't" not in ans and "do not" not in ans and "並不" not in ans and "没有" not in ans and "沒有" not in ans:
                note.append("possible_scuba_hallucination")
        if any(x in ans for x in ("i've noted", "team will research", "follow up", "don't have that", "not something we", "don't offer", "do not offer", "not offered", "outside", "can't help with stock")):
            note.append("gap_handled")
    if "admin" in ans and ("todo" in ans or "to-do" in ans or "to do" in ans):
        note.append("admin_leak")
    if r["id"] == "mt_hours_2" and not any(x in ans for x in ("sunday", "closed", "星期日", "休息")):
        note.append("hours_followup_weak")
    if r["id"] == "mt_avail_then_fee2" and r.get("slot_count"):
        note.append("stuck_on_availability")
    if r["id"] == "fees_phonics" and "hk$" not in ans and "$" not in ans and "fee" not in ans:
        note.append("fee_missing_number")
    if r.get("ms", 0) > 15000:
        note.append("slow_gt_15s")
    if note:
        issues.append((r["id"], note, (r.get("answer") or "")[:350]))

print("\n=== HEURISTIC ISSUES ===")
for iid, note, preview in issues:
    print(iid, note)
    print(" ", preview.replace("\n", " ")[:300])
    print()

for want in ["gap", "escalation", "edge", "multiturn", "fees", "availability", "booking", "hours", "faq", "greeting", "i18n"]:
    print(f"\n######## {want.upper()} ########")
    for r in rows:
        if r["cat"] != want:
            continue
        print(f"\n--- {r['id']} route={r.get('route')} provider={r.get('provider')} cites={r.get('citation_count')} slots={r.get('slot_count')} ms={r['ms']} grade={r.get('grade')} ---")
        print("Q:", r["msg"])
        print("A:", (r.get("answer") or "")[:700])
        tr = r.get("trace") or {}
        steps = tr.get("steps") or []
        print(
            "TRACE",
            tr.get("route"),
            [(s.get("id"), s.get("status"), (s.get("detail") or "")[:50]) for s in steps[:8]],
        )
