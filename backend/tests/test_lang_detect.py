"""Chinese variant detection — Simplified must not false-tie to zh-HK."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llm.lang import (  # noqa: E402
    SIMP_ONLY,
    TRAD_ONLY,
    explain_language_code,
    get_language_code,
)


def test_simplified_course_ask_is_zh_cn_not_tie():
    msg = "你有中文课吗？"
    explained = explain_language_code(msg)
    assert explained["decision"] == "zh-CN"
    vc = explained["variant_counts"] or {}
    assert vc.get("winner") == "simp_only"
    assert vc.get("simp_only", 0) >= 2  # 课 + 吗
    assert vc.get("tie_break") is None
    assert "课" in SIMP_ONLY and "吗" in SIMP_ONLY
    assert "課" in TRAD_ONLY and "嗎" in TRAD_ONLY


def test_particle_ma_variants():
    assert get_language_code("可以吗") == "zh-CN"
    assert get_language_code("可以嗎") == "zh-HK"


def test_traditional_ask_stays_zh_hk():
    assert get_language_code("請問這裏的資料正確嗎？") == "zh-HK"


def test_ambiguous_defaults_zh_hk():
    explained = explain_language_code("好的")
    assert explained["decision"] == "zh-HK"
    assert (explained.get("variant_counts") or {}).get("winner") == "tie"


def test_english():
    assert get_language_code("Tell me about phonics") == "en"


def test_shared_glyphs_not_marked_simplified():
    # 你 is shared; must not tip a Traditional sentence toward zh-CN
    assert "你" not in SIMP_ONLY
    assert "我" not in SIMP_ONLY
    assert "的" not in SIMP_ONLY
