"""Structured centre facts — single source of truth for high-stakes FAQs."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_FACTS_DIR = Path(__file__).resolve().parent.parent / "data" / "facts"

_FEE_RE = re.compile(
    r"("
    r"\b(fee|fees|tuition|price|prices|cost|package\s+fee|monthly\s+fee)\b|"
    r"\bhow\s+much\b(?!\s+(time|longer|long|does\s+it\s+take|minutes?|hours?\b))|"
    r"學費|收費|费用|价钱|價格|多少钱|幾多錢|几多钱"
    r")",
    re.I,
)
_FEE_DURATION_RE = re.compile(
    r"\bhow\s+much\s+(time|longer|long)\b|\bhow\s+long\b|多久|幾耐|几长|多长时间",
    re.I,
)
_WEATHER_POLICY_RE = re.compile(
    r"("
    r"typhoon|rainstorm|\bt[1389]\b|\bt10\b|black\s*rain|amber\s*rain|red\s*rain|"
    r"signal\s*(no\.?\s*)?[138]|severe\s+weather|bad\s+weather|wind\s+signal|"
    r"颱風|台风|風球|风球|黑雨|紅雨|红雨|黃雨|黄雨|黑色暴雨|暴雨警告|"
    r"八號|八号|三號|三号|一號|一号|十號|十号|惡劣天氣|恶劣天气"
    r")",
    re.I,
)
# Avoid matching bare "amber" jewellery etc. — rainstorm context preferred above.
_LOCATION_RE = re.compile(
    r"("
    r"\b(where\s+are\s+you|where\s+is\s+(the\s+)?(centre|center)|address|located|"
    r"location|directions?|how\s+(do\s+i\s+|to\s+)?get\s+there|mtr|parking|"
    r"map\s*link|google\s*maps?)\b|"
    r"地址|點去|怎么去|怎麼去|在哪|邊度|喺邊|喺邊度|港鐵|地铁|地鐵|點搭|怎么搭|泊車|泊车"
    r")",
    re.I,
)
_ALLERGY_RE = re.compile(
    r"\b(allerg(?:y|ies|ic)|peanut|tree[\s-]?nut|nut[\s-]?free|epipen|anaphyla)\b|"
    r"過敏|过敏|花生|堅果|坚果",
    re.I,
)
_ILLNESS_RE = re.compile(
    r"("
    r"\b(fever|temperature|flu|sick|ill|vomit|diarrhea|diarrhoea|covid|"
    r"stay\s+home|keep\s+(them|him|her|my\s+child)\s+home)\b|"
    r"\b\d{2}\s*°?\s*c\b|\b3[89](\.\d)?\s*c\b|"
    r"發燒|发烧|感冒|生病|腹瀉|腹泻|嘔吐|呕吐"
    r")",
    re.I,
)
_PAYMENT_RE = re.compile(
    r"("
    r"\b(how\s+(do\s+i\s+|to\s+)?pay|payment|pay\s+for|bank\s+transfer|fps|"
    r"deposit\s+slip|wire\s+transfer|fps\s*id)\b|"
    r"點樣俾錢|點樣俾學費|点样俾学费|怎么付款|怎麼付款|怎么交学费|怎麼交學費|"
    r"轉數快|转数快|銀行轉帳|银行转账|交學費|交学费|俾學費|俾学费"
    r")",
    re.I,
)
_PAY_METHOD_RE = re.compile(
    r"\b(fps|bank|transfer|deposit\s+slip)\b|轉數快|转数快|銀行|银行|戶口|户口",
    re.I,
)
_TEACHERS_RE = re.compile(
    r"("
    r"\b(teachers?|tutors?|who\s+teaches|teacher\s+qualifications?|staff\s+qualifications?|"
    r"native\s+speakers?)\b|"
    r"老師|老师|教师|導師|导师|資歷|资历|資格|资格|邊個教|谁教"
    r")",
    re.I,
)
_SIBLING_RE = re.compile(
    r"("
    r"\b(sibling|brother|sister|family)\s*(discount|rate|offer|price)?\b|"
    r"\bsecond\s+child\b|兄弟姊妹|兄弟姐妹|手足折扣|第二個仔|第二个孩子"
    r")",
    re.I,
)
_WAITING_RE = re.compile(
    r"("
    r"\b(wait(?:ing)?\s*list|waitlist|class\s+(is\s+)?full|no\s+seats?|full\s+class)\b|"
    r"候補|候补|等候名單|等候名单|滿班|满班|冇位|没位|沒位"
    r")",
    re.I,
)
_PICKUP_RE = re.compile(
    r"("
    r"\b(pick[\s-]?up|drop[\s-]?off|collect(?:ion|or)?|authori[sz]ed\s+pickup|"
    r"who\s+can\s+(pick|collect)|grandparents?\s+pick|helper\s+pick|waiting\s+area)\b|"
    r"接送|接放學|接放学|家長等候|谁可以接|邊個接"
    r")",
    re.I,
)
_MAKEUP_RE = re.compile(
    r"("
    r"\b(make[\s-]?ups?|makeup|reschedul(?:e|ing)|absence\s+policy|miss(?:ed)?\s+(a\s+)?class|"
    r"free\s+make[\s-]?ups?|quota)\b|"
    r"補課|补课|改期|缺席|請假|请假"
    r")",
    re.I,
)

_COURSE_FEE_HINTS = [
    (re.compile(r"phonic|拼音", re.I), "phonics"),
    (re.compile(r"mandarin|putonghua|普通話|普通话|中文", re.I), "mandarin"),
    (re.compile(r"writ|寫作|写作", re.I), "writing"),
    (re.compile(r"play\s*group|遊戲班|游戏班", re.I), "playgroup"),
    (re.compile(r"\bmath\b|數學|数学|numeracy", re.I), "math"),
    (re.compile(r"trial|試堂|试听|assessment|評估|评估", re.I), "trial"),
    (re.compile(r"private|1\s*[:：]?\s*1|一對一|一对一", re.I), "private"),
]


@lru_cache(maxsize=1)
def _fees() -> Dict[str, Any]:
    return json.loads((_FACTS_DIR / "fees.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _weather() -> Dict[str, Any]:
    return json.loads((_FACTS_DIR / "severe_weather.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _ops() -> Dict[str, Any]:
    return json.loads((_FACTS_DIR / "centre_ops.json").read_text(encoding="utf-8"))


def is_fees_intent(message: str) -> bool:
    m = message or ""
    if _FEE_DURATION_RE.search(m):
        return False
    return bool(_FEE_RE.search(m))


def is_severe_weather_intent(message: str) -> bool:
    return bool(_WEATHER_POLICY_RE.search(message or ""))


def is_location_intent(message: str) -> bool:
    return bool(_LOCATION_RE.search(message or ""))


def is_allergy_intent(message: str) -> bool:
    return bool(_ALLERGY_RE.search(message or ""))


def is_illness_intent(message: str) -> bool:
    return bool(_ILLNESS_RE.search(message or ""))


def is_payment_intent(message: str) -> bool:
    m = message or ""
    if not _PAYMENT_RE.search(m):
        return False
    # Explicit pay-method terms win over tuition-amount wording
    if _PAY_METHOD_RE.search(m):
        return True
    # "how much do I pay" / bare 學費 → fees, not payment rails
    if is_fees_intent(m) and re.search(r"\bhow\s+much\b|學費|收费|費用|费用", m, re.I):
        return False
    return True


def is_teachers_intent(message: str) -> bool:
    m = message or ""
    # "change teachers" / mid-term swap → RAG policy, not staff roster
    if re.search(r"\b(chang(?:e|ing)|swap|switch|replace)\b.{0,24}\bteachers?\b", m, re.I):
        return False
    if re.search(r"(換老師|换老师|轉老師|转老师)", m):
        return False
    return bool(_TEACHERS_RE.search(m))


def is_pickup_intent(message: str) -> bool:
    m = message or ""
    if is_waiting_intent(m):
        return False
    return bool(_PICKUP_RE.search(m))


def is_makeup_intent(message: str) -> bool:
    m = message or ""
    # Severe weather makeup questions still belong to weather first
    if is_severe_weather_intent(m) and not re.search(r"make[\s-]?up|補課|补课|quota", m, re.I):
        return False
    return bool(_MAKEUP_RE.search(m))


def is_sibling_intent(message: str) -> bool:
    return bool(_SIBLING_RE.search(message or ""))


def is_waiting_intent(message: str) -> bool:
    return bool(_WAITING_RE.search(message or ""))


def _lang_bucket(lang: str) -> str:
    L = (lang or "en").lower()
    if L.startswith("zh-hk"):
        return "zh-HK"
    if L.startswith("zh"):
        return "zh-CN"
    return "en"


def _detect_fee_focus(message: str) -> Optional[str]:
    for pat, key in _COURSE_FEE_HINTS:
        if pat.search(message or ""):
            return key
    return None


def format_fees_answer(message: str, lang: str = "en") -> Tuple[str, List[Dict[str, Any]]]:
    data = _fees()
    focus = _detect_fee_focus(message)
    bucket = _lang_bucket(lang)
    trial = data["trial_assessment"]
    mats = data["materials_fee_per_term"]
    sib = data["sibling_discount_pct"]
    programmes = data["programmes"]
    private = data["private_1to1"]

    cites = [{"source": "data/facts/fees.json", "title": "Published fee guide"}]

    if focus == "trial":
        if bucket == "zh-HK":
            answer = (
                f"試堂評估（Trial Assessment）約 {trial['duration_min']} 分鐘，"
                f"費用 **HK${trial['fee']}**。"
                f"若於 {trial['credit_days']} 天內報讀正式套餐，可全數扣減。"
            )
        elif bucket == "zh-CN":
            answer = (
                f"试听评估（Trial Assessment）约 {trial['duration_min']} 分钟，"
                f"费用 **HK${trial['fee']}**。"
                f"若于 {trial['credit_days']} 天内报读正式套餐，可全数扣减。"
            )
        else:
            answer = (
                f"Our **Trial Assessment** is about {trial['duration_min']} minutes and costs "
                f"**HK${trial['fee']}**. If you enrol in a package within "
                f"**{trial['credit_days']} days**, it is credited toward your first package."
            )
        return answer, cites

    if focus == "private":
        em = private["english_math_per_45min"]
        md = private["mandarin_per_45min"]
        if bucket.startswith("zh"):
            answer = (
                f"私人一對一（指引價）：英文/數學每 45 分鐘 HK${em['min']}–{em['max']}；"
                f"普通話每 45 分鐘 HK${md['min']}–{md['max']}。"
                f"預繳 8 堂可享 {data['private_pack_discount_pct']}% 優惠。"
            )
        else:
            answer = (
                f"**Private 1:1 guide fees:** English/Math **HK${em['min']}–{em['max']}** / 45 min; "
                f"Mandarin **HK${md['min']}–{md['max']}** / 45 min. "
                f"**{data['private_pack_discount_pct']}%** off a prepaid pack of 8. "
                "Final quote after a short placement chat."
            )
        return answer, cites

    if focus and focus in programmes:
        p = programmes[focus]
        if bucket == "zh-HK":
            answer = (
                f"**{p['label']}** 套餐指引價：{p['package_sessions']} 堂 "
                f"**HK${p['fee_min']:,}–{p['fee_max']:,}**。"
                f"教材費通常每學期 HK${mats['min']}–{mats['max']}（另計）。"
                f"試堂評估 HK${trial['fee']}。"
            )
        elif bucket == "zh-CN":
            answer = (
                f"**{p['label']}** 套餐指引价：{p['package_sessions']} 堂 "
                f"**HK${p['fee_min']:,}–{p['fee_max']:,}**。"
                f"教材费通常每学期 HK${mats['min']}–{mats['max']}（另计）。"
                f"试听评估 HK${trial['fee']}。"
            )
        else:
            answer = (
                f"**{p['label']}** published guide fee: **HK${p['fee_min']:,}–{p['fee_max']:,}** "
                f"for an **{p['package_sessions']}-session** package. "
                f"Materials are usually **HK${mats['min']}–{mats['max']}** per term (separate). "
                f"Trial Assessment: **HK${trial['fee']}**."
            )
        return answer, cites

    # General overview
    lines: List[str] = []
    if bucket == "zh-HK":
        lines.append("以下為 BrightNest **公開指引學費**（實際以程度及套餐為準）：")
        for p in programmes.values():
            lines.append(
                f"- **{p['label']}：** {p['package_sessions']} 堂 HK${p['fee_min']:,}–{p['fee_max']:,}"
            )
        lines.append(f"- **試堂評估：** {trial['duration_min']} 分鐘 HK${trial['fee']}")
        lines.append(
            f"- **教材費：** 通常每學期 HK${mats['min']}–{mats['max']}（另計）"
        )
        lines.append(f"- **兄弟姊妹優惠：** 同學期報讀可享較低套餐 **{sib}%** 折扣")
    elif bucket == "zh-CN":
        lines.append("以下为 BrightNest **公开指引学费**（实际以程度及套餐为准）：")
        for p in programmes.values():
            lines.append(
                f"- **{p['label']}：** {p['package_sessions']} 堂 HK${p['fee_min']:,}–{p['fee_max']:,}"
            )
        lines.append(f"- **试听评估：** {trial['duration_min']} 分钟 HK${trial['fee']}")
        lines.append(
            f"- **教材费：** 通常每学期 HK${mats['min']}–{mats['max']}（另计）"
        )
        lines.append(f"- **兄弟姐妹优惠：** 同学期报读可享较低套餐 **{sib}%** 折扣")
    else:
        lines.append(
            "Here are BrightNest **published guide ranges** (exact quote depends on level/package):"
        )
        for p in programmes.values():
            lines.append(
                f"- **{p['label']}:** HK${p['fee_min']:,}–{p['fee_max']:,} for "
                f"{p['package_sessions']} sessions"
            )
        em = private["english_math_per_45min"]
        md = private["mandarin_per_45min"]
        lines.append(
            f"- **Private 1:1:** English/Math HK${em['min']}–{em['max']} / 45 min; "
            f"Mandarin HK${md['min']}–{md['max']} / 45 min "
            f"({data['private_pack_discount_pct']}% off prepaid pack of 8)"
        )
        lines.append(
            f"- **Trial Assessment:** HK${trial['fee']} ({trial['duration_min']} min)"
        )
        lines.append(
            f"- **Materials:** usually HK${mats['min']}–{mats['max']} per term"
        )
        lines.append(
            f"- **Sibling discount:** **{sib}%** off the lower package when two siblings "
            "enrol in the same term"
        )
    return "\n".join(lines), cites


def format_severe_weather_answer(message: str, lang: str = "en") -> Tuple[str, List[Dict[str, Any]]]:
    data = _weather()
    bucket = _lang_bucket(lang)
    low = (message or "").lower()
    cites = [
        {"source": "data/facts/severe_weather.json", "title": "Severe weather policy"},
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


def format_trial_info_answer(lang: str = "en") -> Tuple[str, List[Dict[str, Any]]]:
    data = _fees()
    trial = data["trial_assessment"]
    bucket = _lang_bucket(lang)
    cites = [
        {"source": "data/facts/fees.json", "title": "Trial Assessment"},
        {"source": "courses/trial_assessment", "title": "Trial assessment"},
    ]
    if bucket == "zh-HK":
        answer = (
            f"我們提供 **試堂評估**（約 {trial['duration_min']} 分鐘，HK${trial['fee']}），"
            "包括簡短家長面談與適齡學習觀察，方便推薦適合課程。"
            f"若於 {trial['credit_days']} 天內報讀套餐，試堂費可扣減。"
            "想查看可預約時段，可以說「show trial availability」或「有冇試堂位」。"
        )
    elif bucket == "zh-CN":
        answer = (
            f"我们提供 **试听评估**（约 {trial['duration_min']} 分钟，HK${trial['fee']}），"
            "包括简短家长面谈与适龄学习观察，方便推荐适合课程。"
            f"若于 {trial['credit_days']} 天内报读套餐，试听费可扣减。"
            "若要查看可预约时段，可以说「show trial availability」。"
        )
    else:
        answer = (
            f"Yes — we offer a **Trial Assessment** (~{trial['duration_min']} minutes, "
            f"**HK${trial['fee']}**). It includes a short parent chat and an age-appropriate "
            "learning check so we can recommend a good fit. "
            f"If you enrol within **{trial['credit_days']} days**, the fee is credited "
            "toward your first package.\n\n"
            "If you want open seats, ask for **trial availability** or tap Show availability."
        )
    return answer, cites


def format_pets_policy(lang: str = "en") -> Tuple[str, List[Dict[str, Any]]]:
    bucket = _lang_bucket(lang)
    cites = [
        {"source": "data/facts/centre_ops.json", "title": "Pets policy"},
        {"source": "policies/pets", "title": "Pets Policy"},
    ]
    if bucket.startswith("zh"):
        answer = (
            "為安全及衞生理由，**寵物不能進入課室、陪同試堂，或於課堂期間逗留教學區**。"
            "如需導盲／輔助動物，請事先聯絡接待處。"
            "我們可以安排試堂評估給小朋友；如需查看時段，請告訴我們想看哪一個課程的空位。"
        )
    else:
        answer = (
            "For safety and hygiene, **pets cannot join classes, sit in on trials, "
            "or wait in teaching areas**. Guide/assistance animals: contact reception in advance.\n\n"
            "We’re happy to arrange a **Trial Assessment** for your child — "
            "ask for trial availability if you’d like to see open seats."
        )
    return answer, cites


def format_location_answer(lang: str = "en") -> Tuple[str, List[Dict[str, Any]]]:
    loc = _ops()["location"]
    bucket = _lang_bucket(lang)
    cites = [
        {"source": "data/facts/centre_ops.json", "title": "Centre location"},
        {"source": "institution/location", "title": "Location"},
    ]
    if bucket == "zh-HK":
        answer = (
            f"**地址：** {loc['address']}\n"
            f"**港鐵：** {loc['mtr']}\n"
            f"**泊車：** {loc['parking']}\n"
            f"**進入：** {loc['access']}\n"
            f"地圖：{loc['map']}"
        )
    elif bucket == "zh-CN":
        answer = (
            f"**地址：** {loc['address']}\n"
            f"**地铁：** {loc['mtr']}\n"
            f"**停车：** {loc['parking']}\n"
            f"**进入：** {loc['access']}\n"
            f"地图：{loc['map']}"
        )
    else:
        answer = (
            f"**Address:** {loc['address']}\n"
            f"**MTR:** {loc['mtr']}\n"
            f"**Parking:** {loc['parking']}\n"
            f"**Access:** {loc['access']}\n"
            f"Map: {loc['map']}"
        )
    return answer, cites


def format_allergy_answer(lang: str = "en") -> Tuple[str, List[Dict[str, Any]]]:
    bucket = _lang_bucket(lang)
    cites = [
        {"source": "data/facts/centre_ops.json", "title": "Allergy policy"},
        {"source": "faq/allergy_safety", "title": "Allergy & safety"},
    ]
    if bucket.startswith("zh"):
        answer = (
            "我哋係 **堅果敏感意識** 中心：\n"
            "- 請唔好帶花生／樹堅果分享小食\n"
            "- 報名表請列明過敏；老師登記冊會標示\n"
            "- 如有嚴重過敏，請帶已標名處方急救藥\n"
            "- 生日小食需提前 24 小時通知並經老師同意"
        )
    else:
        answer = (
            "We are a **nut-aware** centre:\n"
            "- Please do not bring peanut or tree-nut snacks for sharing\n"
            "- List allergies on the enrolment form; we flag them on the class register\n"
            "- For severe allergies, bring labelled prescribed emergency medication\n"
            "- Birthday treats only with 24 hours’ notice and teacher approval"
        )
    return answer, cites


def format_illness_answer(lang: str = "en") -> Tuple[str, List[Dict[str, Any]]]:
    ill = _ops()["illness"]
    bucket = _lang_bucket(lang)
    cites = [
        {"source": "data/facts/centre_ops.json", "title": "Illness / fever policy"},
    ]
    if bucket.startswith("zh"):
        answer = (
            f"請小朋友留喺屋企：發燒 **{ill['fever_celsius']}°C 或以上** 唔好返學；"
            "退燒後 **24 小時**（唔使食退燒藥）先返。\n"
            f"{ill['makeup']}"
        )
    else:
        answer = (
            f"**{ill['stay_home_rule']}**\n"
            f"{ill['makeup']}"
        )
    return answer, cites


def format_payment_answer(lang: str = "en") -> Tuple[str, List[Dict[str, Any]]]:
    pay = _ops()["payment"]
    bucket = _lang_bucket(lang)
    cites = [
        {"source": "data/facts/centre_ops.json", "title": "How to pay"},
        {"source": "guides/how_to_pay", "title": "How to Pay"},
    ]
    if bucket.startswith("zh"):
        answer = (
            "付款方式：\n"
            f"1) **銀行入數** — {pay['bank']}；戶名 {pay['account_name']}；"
            f"銀行編號 {pay['bank_code']}；帳號 {pay['account_no']}\n"
            f"2) **轉數快 FPS** — ID：{pay['fps_id']}（類別選「其他」）\n"
            f"入數後請電郵單據至 {pay['slip_email']}。"
        )
    else:
        answer = (
            "You can pay by:\n"
            f"1) **Bank deposit** — {pay['bank']}; account **{pay['account_name']}**; "
            f"bank code **{pay['bank_code']}**; account **{pay['account_no']}**\n"
            f"2) **FPS** — ID **{pay['fps_id']}** (category: Others)\n"
            f"Please email the deposit slip to **{pay['slip_email']}** for our records."
        )
    return answer, cites


def format_teachers_answer(lang: str = "en") -> Tuple[str, List[Dict[str, Any]]]:
    t = _ops()["teachers"]
    bucket = _lang_bucket(lang)
    cites = [
        {"source": "data/facts/centre_ops.json", "title": "Teachers"},
        {"source": "institution/teachers", "title": "Our Teachers"},
    ]
    bullets = "\n".join(f"- {h}" for h in t["highlights"])
    if bucket.startswith("zh"):
        answer = f"我哋嘅老師都係雙語幼教／小學經驗導師，並完成校內 safeguarding 培訓。\n{bullets}"
    else:
        answer = f"{t['summary']}\n{bullets}\n\nAsk for a programme’s availability if you’d like to see who is teaching open seats."
    return answer, cites


def format_pickup_answer(lang: str = "en") -> Tuple[str, List[Dict[str, Any]]]:
    p = _ops()["pickup"]
    bucket = _lang_bucket(lang)
    cites = [
        {"source": "data/facts/centre_ops.json", "title": "Drop-off & pickup"},
        {"source": "faq/dropoff_pickup", "title": "Drop-off, Pickup & Waiting"},
    ]
    if bucket.startswith("zh"):
        answer = (
            f"- {p['playgroup_stay']}\n- {p['other_wait']}\n- {p['arrive']}\n"
            f"- {p['late']}\n- {p['authorised']}"
        )
    else:
        answer = (
            f"- {p['playgroup_stay']}\n- {p['other_wait']}\n- {p['arrive']}\n"
            f"- {p['late']}\n- {p['authorised']}"
        )
    return answer, cites


def format_makeup_answer(lang: str = "en") -> Tuple[str, List[Dict[str, Any]]]:
    m = _ops()["makeup"]
    bucket = _lang_bucket(lang)
    cites = [
        {"source": "data/facts/centre_ops.json", "title": "Absence & make-up"},
        {"source": "policies/absence_makeup", "title": "Absence & Make-up Policy"},
    ]
    if bucket.startswith("zh"):
        answer = (
            f"- 請假請至少提前 **{m['notice_hours']} 小時** 書面通知\n"
            f"- 免費補課額：8 堂套餐 **{m['quota_8']}** 次；10 堂套餐 **{m['quota_10']}** 次\n"
            f"- 超出額度每次行政費 **HK${m['extra_fee_hkd']}**\n"
            f"- {m['weather_no_quota']}\n"
            f"- {m['window']}"
        )
    else:
        answer = (
            f"- Please give at least **{m['notice_hours']} hours’** written notice if your child will miss a class\n"
            f"- Free make-up quota: **{m['quota_8']}** per 8-session pack, **{m['quota_10']}** per 10-session pack\n"
            f"- Beyond quota: **HK${m['extra_fee_hkd']}** admin fee per extra make-up\n"
            f"- {m['weather_no_quota']}\n"
            f"- {m['window']}\n"
            "Staff confirm arrangements on WhatsApp/email — chat does not book make-ups."
        )
    return answer, cites


def format_sibling_answer(lang: str = "en") -> Tuple[str, List[Dict[str, Any]]]:
    pct = _fees()["sibling_discount_pct"]
    bucket = _lang_bucket(lang)
    cites = [
        {"source": "data/facts/fees.json", "title": "Sibling discount"},
        {"source": "faq/sibling_family_discount", "title": "Sibling / Family Discount"},
        {"source": "faq/fees_overview", "title": "Fees Overview"},
    ]
    if bucket.startswith("zh"):
        answer = (
            f"同學期兩位兄弟姊妹同時報讀，較低學費套餐可享 **{pct}%** 折扣。"
            "如需完整學費表，可再問「學費幾多」。"
        )
    else:
        answer = (
            f"**Sibling discount:** **{pct}%** off the lower package when two siblings "
            "enrol in the same term.\n"
            "Ask for full tuition ranges if you’d like the programme fee table."
        )
    return answer, cites


def format_waiting_answer(lang: str = "en") -> Tuple[str, List[Dict[str, Any]]]:
    bucket = _lang_bucket(lang)
    cites = [
        {"source": "faq/waiting_list", "title": "Waiting List"},
    ]
    if bucket.startswith("zh"):
        answer = (
            "如心儀時段已滿：\n"
            "1. 放入 **候補名單**（通常 **1–3 星期**內有位）\n"
            "2. 可先入讀程度相近、最近空缺的小組\n"
            "3. 同學期心儀學位出缺時，提供 **免費轉班**\n"
            "請留言課程、日子／時間同小朋友年齡，或先預約試堂評估。"
        )
    else:
        answer = (
            "If your preferred slot is full:\n"
            "1. We place your child on the **waitlist** (usually moves within **1–3 weeks**).\n"
            "2. You may start in the **nearest same-level group** so learning is not delayed.\n"
            "3. When your preferred seat opens in the **same term**, we offer a **free transfer**.\n"
            "Share the programme, preferred day/time, and child’s age — or book a Trial Assessment."
        )
    return answer, cites
