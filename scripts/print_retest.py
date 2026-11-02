import json

d = json.load(open("scripts/retest_results.json", encoding="utf-8"))
print("FAILURE", d["failure_retest"]["pass"], "/", len(d["failure_retest"]["rows"]))
for r in d["failure_retest"]["rows"]:
    if not r["ok"]:
        print(" FAIL", r)
print("\nBOMB")
for r in d["bombard"]["rows"]:
    print(
        f"{r['id']:14} {str(r['route']):28} {r['ms']:5}ms | {r['answer_preview'][:110]}"
    )
