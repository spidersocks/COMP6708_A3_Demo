from pathlib import Path

for p in Path("content").rglob("*.md"):
    t = p.read_text(encoding="utf-8")
    if "3HK" in t or "HK\\," in t:
        print("BAD", p)
        for i, line in enumerate(t.splitlines(), 1):
            if "3HK" in line or "HK\\," in line:
                print(f"  {i}: {line[:140]}")

# Fix fees_overview table row
p = Path("content/en/faq/fees_overview.md")
t = p.read_text(encoding="utf-8")
t2 = t.replace(
    "| Phonics (K1–P2) | 8 sessions | HK$3HK\\,200–3,680 (8-session guide)",
    "| Phonics (K1–P2) | 8 sessions | HK$3,200–3,680 |",
)
# Also fix if broken differently
import re

t2 = re.sub(
    r"\| Phonics \(K1–P2\) \| 8 sessions \|.*",
    "| Phonics (K1–P2) | 8 sessions | HK$3,200–3,680 |",
    t2,
)
if t2 != t:
    p.write_text(t2, encoding="utf-8")
    print("fixed fees_overview")

# Generic fix for accidental replacements
for p in Path("content").rglob("*.md"):
    t = p.read_text(encoding="utf-8")
    t2 = t.replace("HK$3HK\\,200–3,680 (8-session guide)", "HK$3,200–3,680")
    t2 = t2.replace("HK$3HK,200–3,680 (8-session guide)", "HK$3,200–3,680")
    if t2 != t:
        p.write_text(t2, encoding="utf-8")
        print("fixed", p)
