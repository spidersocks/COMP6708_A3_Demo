"""Live tool facts — severe weather policy (static FAQs are RAG)."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Curated JSON lives under /app/facts (NOT /app/data — SAE mounts a volume there)
_FACTS_DIR = Path(__file__).resolve().parent.parent / "facts"

_WEATHER_POLICY_RE = re.compile(
    r"("
    r"typhoon|rainstorm|\bt[1389]\b|\bt10\b|black\s*rain|amber\s*rain|red\s*rain|"
    r"signal\s*(no\.?\s*)?[138]|severe\s+weather|bad\s+weather|wind\s+signal|"
    r"颱風|台风|風球|风球|黑雨|紅雨|红雨|黃雨|黄雨|黑色暴雨|暴雨警告|"
    r"八號|八号|三號|三号|一號|一号|十號|十号|惡劣天氣|恶劣天气"
    r")",
    re.I,
)


@lru_cache(maxsize=1)
def _weather() -> Dict[str, Any]:
    return json.loads((_FACTS_DIR / "severe_weather.json").read_text(encoding="utf-8"))


def is_severe_weather_intent(message: str) -> bool:
    return bool(_WEATHER_POLICY_RE.search(message or ""))


def _lang_bucket(lang: str) -> str:
    L = (lang or "en").lower()
    if L.startswith("zh-hk"):
        return "zh-HK"
    if L.startswith("zh"):
        return "zh-CN"
    return "en"


def format_severe_weather_answer(message: str, lang: str = "en") -> Tuple[str, List[Dict[str, Any]]]:
    data = _weather()
    bucket = _lang_bucket(lang)
    low = (message or "").lower()
    cites = [
        {"source": "facts/severe_weather.json", "title": "Severe weather policy"},
        {"source": "policies/severe_weather", "title": "Severe Weather Arrangements"},
    ]

    asks_t8 = bool(
        re.search(r"\bt\s*8\b|signal\s*(no\.?\s*)?8|八號|八号|t8|no\.?\s*8", message or "", re.I)
    )
    asks_t3 = bool(
        re.search(r"\bt\s*3\b|signal\s*(no\.?\s*)?3|三號|三号", message or "", re.I)
    )
    asks_black = bool(re.search(r"black\s*rain|黑雨|黑色暴雨", message or "", re.I))

    if bucket == "zh-HK":
        base = (
            "惡劣天氣安排：\n"
            "- **黃／紅雨警告**、**一號或三號風球**：課堂照常進行。\n"
            "- **黑雨**或**八號或以上風球**（含預先改發八號）：**暫停課堂**。\n"
            f"- {data['make_up']}\n"
            f"- {data['notify']}"
        )
        if asks_t8 or asks_black:
            base = (
                "若懸掛 **八號或以上風球** 或 **黑雨警告**，BrightNest **會暫停所有課堂**。\n\n"
                + base
            )
        elif asks_t3:
            base = (
                "在 **三號風球** 下，課堂 **照常進行**（我們會持續留意天氣）。\n\n" + base
            )
    elif bucket == "zh-CN":
        base = (
            "恶劣天气安排：\n"
            "- **黄／红雨警告**、**一号或三号风球**：课程照常进行。\n"
            "- **黑雨**或**八号及以上风球**（含预先改发八号）：**暂停课程**。\n"
            f"- {data['make_up']}\n"
            f"- {data['notify']}"
        )
        if asks_t8 or asks_black:
            base = (
                "若悬挂 **八号或以上风球** 或 **黑雨警告**，BrightNest **会暂停所有课程**。\n\n"
                + base
            )
        elif asks_t3:
            base = (
                "在 **三号风球** 下，课程 **照常进行**（我们会持续留意天气）。\n\n" + base
            )
    else:
        base = (
            "BrightNest severe-weather policy:\n"
            "- Classes **proceed** under Amber/Red rainstorm warnings and Typhoon Signals **T1 / T3**.\n"
            "- Classes are **suspended** under **Black Rain** or Typhoon Signal **No. 8 or above** "
            "(including Pre-No. 8).\n"
            f"- {data['make_up']}\n"
            f"- {data['notify']}"
        )
        if asks_t8 or asks_black:
            base = (
                "Under **Typhoon Signal No. 8+** or **Black Rain**, BrightNest **suspends all classes**.\n\n"
                + base
            )
        elif asks_t3:
            base = (
                "Under **Typhoon Signal No. 3**, classes **continue as usual** "
                "(we keep monitoring conditions).\n\n" + base
            )
        elif "typhoon" in low or "rainstorm" in low or "weather" in low:
            pass
    return base, cites
