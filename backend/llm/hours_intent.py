"""Opening-hours intent detection (portable, no Bedrock LLM assist).

Improvements vs production soft spots:
- Explicitly catch \"open tomorrow / Saturday / public holiday\" without requiring
  the word \"hours\".
- Hard-exclude booking/availability so trial-slot questions don't steal the
  deterministic hours path.
- Detect short follow-ups (\"tomorrow?\", \"星期六呢?\") so multi-turn demos work.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from llm.opening_hours import _HOLIDAY_KEYWORDS

_EN_STRONG = [
    r"\bopen(?:ing)?\b",
    r"\bhours?\b",
    r"\bclosed?\b",
    r"\bbusiness hours?\b",
    r"\bpublic holiday\b",
    r"\bare you (?:open|closed)\b",
    r"\bis (?:the )?(?:centre|center|school) open\b",
]
_EN_WEAK = [
    r"\btomorrow\b",
    r"\btoday\b",
    r"\b(?:next|this)\s+(?:week|mon|tue|wed|thu|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    r"\b(mon|tue|wed|thu|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
]

_ZH_HK_STRONG = [
    r"營業|營運|開放|開門|關門|收(工|舖|店)|幾點(開|收)|而家開",
    r"公眾假期|假期|休息",
]
_ZH_HK_WEAK = [
    r"今日|聽日|後日|下周|下星期|星期[一二三四五六日天]|周[一二三四五六日天]|週[一二三四五六日天]",
]

_ZH_CN_STRONG = [
    r"营业|开放|开门|关门|几点(开|关)|现在开",
    r"公众假期|公休日|假期|休息",
]
_ZH_CN_WEAK = [
    r"今天|明天|后天|下周|星期[一二三四五六日天]|周[一二三四五六日天]",
]

_WEATHER = [
    r"typhoon|rainstorm|t[1389]|t10|\bt8\b|black rain|amber|red rain",
    r"颱風|台风|風球|风球|黑雨|紅雨|红雨|黃雨|黄雨|八號|八号|三號|三号",
]

# Booking / availability — never treat as opening-hours.
# NOTE: do NOT match bare "opening(s)" — that steals "opening hours".
_AVAIL_NEG = re.compile(
    r"\b(availab(?:le|ility)?|time\s*slots?|open\s+slots?|bookings?\b|book\s+a\b|"
    r"trial\s*class|timetable|"
    r"有冇位|有没有位|空位|預約|预约|試堂|试听|档期|檔期|補課|补课)\b",
    re.IGNORECASE,
)

_FEE_NEG = re.compile(
    r"\b(fee|fees|tuition|price|cost|how much)\b|學費|收費|费用|价钱|價格",
    re.IGNORECASE,
)

_FOLLOWUP_ONLY = re.compile(
    r"^\s*(?:"
    r"tomorrow|today|yesterday|"
    r"(?:this|next)\s+(?:mon|tue|wed|thu|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday)(?:day)?"
    r"|mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?"
    r"|今天|今日|明天|聽日|后天|後日|"
    r"星期[一二三四五六日天]|周[一二三四五六日天]|週[一二三四五六日天]"
    r"|呢\??|嗎\??|吗\??"
    r")[\s?？!！.。]*$",
    re.IGNORECASE,
)

_ALL_HOLIDAY_KW = sorted(
    {kw for terms in _HOLIDAY_KEYWORDS.values() for kw in terms},
    key=len,
    reverse=True,
)


def _score(message: str, patterns: List[str]) -> Tuple[int, List[str]]:
    hits: List[str] = []
    for pat in patterns:
        if re.search(pat, message or "", flags=re.IGNORECASE):
            hits.append(pat)
    return len(hits), hits


def _holiday_hits(message: str) -> List[str]:
    msg = message or ""
    low = msg.lower()
    hits = []
    for kw in _ALL_HOLIDAY_KW:
        if re.search(r"[a-zA-Z]", kw):
            if re.search(r"\b" + re.escape(kw.lower()) + r"\b", low):
                hits.append(kw)
        elif kw in msg:
            hits.append(kw)
    return hits


def detect_opening_hours_intent(message: str, lang: str = "en") -> Tuple[bool, Dict[str, Any]]:
    m = message or ""
    L = (lang or "en").lower()
    if L.startswith("zh-hk"):
        strong, weak = _ZH_HK_STRONG, _ZH_HK_WEAK
    elif L.startswith("zh-cn") or L == "zh":
        strong, weak = _ZH_CN_STRONG, _ZH_CN_WEAK
    else:
        strong, weak = _EN_STRONG, _EN_WEAK

    strong_score, strong_hits = _score(m, strong)
    weak_score, weak_hits = _score(m, weak)
    weather_score, weather_hits = _score(m, _WEATHER)
    hol_hits = _holiday_hits(m)

    # "Are you open tomorrow?" — weak day + open/closed already in strong.
    # Also allow weak-only when phrased as open-question with 呢/吗 and a day.
    is_intent = (
        strong_score > 0
        or bool(hol_hits)
        or weather_score > 0
        or (weak_score > 0 and strong_score > 0)
        or (weak_score > 0 and bool(re.search(r"\b(open|closed|hours)\b|開|开|休息|營業|营业", m, re.I)))
    )

    debug: Dict[str, Any] = {
        "is_intent": is_intent,
        "strong_hits": strong_hits,
        "weak_hits": weak_hits,
        "weather_hits": weather_hits,
        "holiday_hits": hol_hits,
        "overridden_by_avail": False,
        "overridden_by_fees": False,
    }

    if _AVAIL_NEG.search(m):
        is_intent = False
        debug["is_intent"] = False
        debug["overridden_by_avail"] = True
    if _FEE_NEG.search(m) and strong_score == 0 and not hol_hits:
        is_intent = False
        debug["is_intent"] = False
        debug["overridden_by_fees"] = True

    return is_intent, debug


def is_general_hours_query(message: str, lang: str = "en") -> bool:
    m = (message or "").lower()
    specific = [
        r"\b(mon|tue|wed|thu|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        r"\b(today|tomorrow|yesterday|next week|this week)\b",
        r"星期[一二三四五六日天]|周[一二三四五六日天]|週[一二三四五六日天]",
        r"今天|今日|明天|聽日|后天|後日|下周|下星期",
        r"\d{1,2}\s*(月|日|号|號)",
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{1,2}\b",
        r"\b\d{1,2}/\d{1,2}\b",
    ]
    if any(re.search(p, message or "", flags=re.IGNORECASE) for p in specific):
        return False
    if _holiday_hits(message or ""):
        return False
    return True


def is_hours_followup(message: str) -> bool:
    """True for bare relative/weekday follow-ups like 'tomorrow?' / '星期六呢?'."""
    return bool(_FOLLOWUP_ONLY.match((message or "").strip()))


def recent_was_hours(history: List[Dict], lookback: int = 4) -> bool:
    """Did a recent bot/user turn look like opening-hours?"""
    for msg in reversed(history[-lookback:]):
        text = msg.get("message") or ""
        ok, _ = detect_opening_hours_intent(text, "en")
        if ok:
            return True
        # Bot deterministic hours answers often mention open/closed hours
        if re.search(r"\b(open|closed|hours|營業|营业|休息)\b", text, re.I):
            if msg.get("role") == "bot":
                return True
    return False


def effective_hours_message(message: str, history: List[Dict]) -> str:
    """
    For short follow-ups, stitch the last substantive user hours question so
    date parsing sees both 'opening hours' context and 'tomorrow'.
    """
    if not is_hours_followup(message):
        return message
    prior_user = None
    for msg in reversed(history):
        if msg.get("role") != "user":
            continue
        text = (msg.get("message") or "").strip()
        if not text or is_hours_followup(text):
            continue
        prior_user = text
        break
    if prior_user:
        return f"{prior_user}\n{message}"
    return message
