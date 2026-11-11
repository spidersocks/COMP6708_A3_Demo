"""Audit BrightNest KB: translation parity, topic gaps, metadata, retrieval consistency."""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "content"
sys.path.insert(0, str(ROOT / "backend"))

LANGS = ("en", "zh-HK", "zh-CN")
API = "http://127.0.0.1:8000"

# Parent-facing topics we expect covered (relative stem under lang/)
EXPECTED_TOPICS = [
    "centre/getting_here",
    "attendance/dropoff_pickup",
    "attendance/authorized_pickup",
    "policies/allergy_safety",
    "fees/tuition_enquiry",
    "fees/fees_overview",
    "admissions/trial_class",
    "assistant/staff_escalation",
    "fees/sibling_family_discount",
    "attendance/late_arrival",
    "policies/online_classes_livestream",
    "policies/dress_code_uniform",
    "classroom/birthdays_celebrations",
    "attendance/package_freeze_hold",
    "centre/facilities",
    "classroom/class_observation_visits",
    "classroom/changing_teachers",
    "admissions/waiting_list",
    "classroom/child_progress",
    "assistant/greeting",
    "assistant/thanks_acknowledgement",
    "centre/teachers",
    "centre/location",
    "attendance/absence_makeup",
    "policies/severe_weather",
    "policies/pets",
    "admissions/trial_assessment",
    "classroom/bilingual_classroom",
    "centre/term_break_hours",
]

# Key retrieval probes: (id, query, expect_folder_or_source_substr, expect_any_in_answer)
RETRIEVAL_PROBES = [
    ("teachers", "Tell me about your teachers", "teachers", ["Avery", "CELTA", "teacher"]),
    ("pickup", "Who can pick up my child?", "pickup", ["authoris", "collect", "18:15"]),
    ("makeup", "make-up class policy", "makeup", ["quota", "4", "100"]),
    ("fees", "How much is phonics tuition?", "fee", ["3,200", "3200", "Phonics"]),
    ("location", "Where is the centre? MTR?", "location", ["Central", "Queen", "MTR", "D2"]),
    ("allergy", "peanut allergy", "allerg", ["nut", "peanut", "allerg"]),
    ("weather", "typhoon signal 8 classes", "weather", ["8", "suspend"]),
    ("sibling", "sibling discount", "sibling", ["sibling", "discount", "%"]),
    ("online", "online livestream classes", "online", ["online", "livestream", "not"]),
    ("dress", "dress code uniform", "dress", ["dress", "uniform", "casual"]),
]


def _stem_rel(path: Path, lang: str) -> str:
    rel = path.relative_to(CONTENT / lang).as_posix()
    if rel.endswith(".md"):
        rel = rel[:-3]
    return rel


def scan_corpus() -> Dict[str, Any]:
    by_lang: Dict[str, Set[str]] = {lang: set() for lang in LANGS}
    meta_issues: List[Dict[str, Any]] = []
    empty_files: List[str] = []
    frontmatter_missing: List[str] = []

    for lang in LANGS:
        root = CONTENT / lang
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.md")):
            stem = _stem_rel(path, lang)
            by_lang[lang].add(stem)
            text = path.read_text(encoding="utf-8", errors="replace")
            if len(text.strip()) < 40:
                empty_files.append(f"{lang}/{stem}")
            # frontmatter
            meta: Dict[str, Any] = {}
            if text.startswith("---"):
                end = text.find("\n---", 3)
                if end > 0:
                    block = text[3:end]
                    for line in block.splitlines():
                        if ":" in line:
                            k, v = line.split(":", 1)
                            meta[k.strip()] = v.strip().strip("\"'")
            else:
                frontmatter_missing.append(f"{lang}/{stem}")

            folder_guess = stem.split("/")[0] if "/" in stem else ""
            title = meta.get("title") or meta.get("canonical") or ""
            source = meta.get("source") or ""
            folder = meta.get("folder") or ""
            issues = []
            if not title:
                issues.append("missing_title")
            if folder and folder_guess and folder != folder_guess and folder not in stem:
                issues.append(f"folder_mismatch:{folder}!={folder_guess}")
            if source and stem not in source.replace("\\", "/") and Path(stem).name not in source:
                # soft — source may be logical id
                pass
            if issues:
                meta_issues.append({"file": f"{lang}/{stem}", "issues": issues, "meta": meta})

    # translation parity (use en as canonical)
    en = by_lang["en"]
    missing_zh_hk = sorted(en - by_lang["zh-HK"])
    missing_zh_cn = sorted(en - by_lang["zh-CN"])
    only_zh_hk = sorted(by_lang["zh-HK"] - en)
    only_zh_cn = sorted(by_lang["zh-CN"] - en)
    only_en = sorted(en - by_lang["zh-HK"] - by_lang["zh-CN"])

    topic_gaps = []
    for topic in EXPECTED_TOPICS:
        present = {lang: (topic in by_lang[lang]) for lang in LANGS}
        if not all(present.values()):
            topic_gaps.append({"topic": topic, "present": present})

    return {
        "counts": {lang: len(by_lang[lang]) for lang in LANGS},
        "missing_zh_HK_vs_en": missing_zh_hk,
        "missing_zh_CN_vs_en": missing_zh_cn,
        "only_zh_HK": only_zh_hk,
        "only_zh_CN": only_zh_cn,
        "en_without_any_zh": [s for s in en if s not in by_lang["zh-HK"] and s not in by_lang["zh-CN"]],
        "topic_gaps": topic_gaps,
        "empty_or_tiny": empty_files,
        "frontmatter_missing": frontmatter_missing[:40],
        "frontmatter_missing_count": len(frontmatter_missing),
        "meta_issues": meta_issues[:40],
        "meta_issue_count": len(meta_issues),
    }


def post_chat(msg: str, sid: str) -> Dict[str, Any]:
    body = json.dumps({"message": msg, "session_id": sid, "debug": True}).encode()
    req = urllib.request.Request(
        f"{API}/chat", data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=90) as resp:
        payload = json.loads(resp.read().decode())
    return {"ms": int((time.perf_counter() - t0) * 1000), "payload": payload}


def retrieval_audit() -> List[Dict[str, Any]]:
    rows = []
    for pid, query, needle, expect_any in RETRIEVAL_PROBES:
        try:
            result = post_chat(query, f"kb-ret-{pid}")
            p = result["payload"]
            ans = p.get("answer") or ""
            low = ans.lower()
            route = (p.get("trace") or {}).get("route") or p.get("provider")
            cites = p.get("citations") or []
            cite_blob = " ".join(
                str((c.get("source") or "") + " " + (c.get("title") or "")) for c in cites
            ).lower()
            cite_ok = needle.lower() in cite_blob or needle.lower() in low
            content_ok = any(frag.lower() in low for frag in expect_any)
            rows.append(
                {
                    "id": pid,
                    "query": query,
                    "route": route,
                    "ms": result["ms"],
                    "cite_match": cite_ok,
                    "content_ok": content_ok,
                    "ok": cite_ok and content_ok,
                    "citations": [
                        {"source": c.get("source"), "title": c.get("title")} for c in cites[:3]
                    ],
                    "answer_preview": ans[:200].replace("\n", " "),
                }
            )
        except Exception as e:
            rows.append({"id": pid, "query": query, "ok": False, "error": str(e)})
        time.sleep(0.1)
    return rows


def offline_chunk_meta_sample() -> Dict[str, Any]:
    """Sample metadata from chunking without requiring Chroma warm."""
    try:
        from rag.chunking import build_chunks
        from config import SETTINGS

        chunks = build_chunks(SETTINGS.content_dir, chunk_size=900, overlap=120)
        folders = defaultdict(int)
        langs = defaultdict(int)
        missing_folder = 0
        missing_title = 0
        for c in chunks:
            md = c.metadata or {}
            folders[str(md.get("folder") or "(none)")] += 1
            langs[str(md.get("language") or md.get("lang") or "(none)")] += 1
            if not md.get("folder"):
                missing_folder += 1
            if not md.get("title"):
                missing_title += 1
        return {
            "chunk_count": len(chunks),
            "folders": dict(sorted(folders.items(), key=lambda x: -x[1])),
            "languages": dict(langs),
            "missing_folder": missing_folder,
            "missing_title": missing_title,
        }
    except Exception as e:
        return {"error": str(e)}


def main() -> None:
    corpus = scan_corpus()
    chunks = offline_chunk_meta_sample()
    retrieval = []
    api_ok = False
    try:
        with urllib.request.urlopen(f"{API}/healthz", timeout=5) as r:
            api_ok = r.status == 200
    except Exception:
        api_ok = False
    if api_ok:
        retrieval = retrieval_audit()

    report = {
        "corpus": corpus,
        "chunks": chunks,
        "retrieval": retrieval,
        "retrieval_pass": sum(1 for r in retrieval if r.get("ok")),
        "retrieval_total": len(retrieval),
        "api_reachable": api_ok,
    }
    out = ROOT / "scripts" / "kb_audit_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== KB COUNTS ===")
    print(corpus["counts"])
    print(f"missing zh-HK vs en: {len(corpus['missing_zh_HK_vs_en'])}")
    print(f"missing zh-CN vs en: {len(corpus['missing_zh_CN_vs_en'])}")
    print(f"topic_gaps: {len(corpus['topic_gaps'])}")
    for g in corpus["topic_gaps"][:15]:
        print("  gap", g)
    print(f"frontmatter_missing: {corpus['frontmatter_missing_count']}")
    print(f"meta_issues: {corpus['meta_issue_count']}")
    print("=== CHUNKS ===")
    print(json.dumps(chunks, indent=2)[:800])
    print("=== RETRIEVAL ===")
    print(f"{report['retrieval_pass']}/{report['retrieval_total']} pass")
    for r in retrieval:
        flag = "OK" if r.get("ok") else "FAIL"
        print(f"  {flag} {r.get('id')} route={r.get('route')} cite={r.get('cite_match')} content={r.get('content_ok')}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
