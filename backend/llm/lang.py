"""Language / Chinese-variant detection for BrightNest replies.

Decision:
  1) CJK vs Latin letter ratio → en vs Chinese
  2) Among Chinese, majority of *distinctive* Simplified vs Traditional chars
  3) Ties / no distinctive chars → zh-HK (Hong Kong default)
  4) Optional Accept-Language override for zh-Hans / zh-Hant

Distinctive characters are one-to-one simp↔trad variants. A previous bulk list
omitted high-frequency particles (吗/嗎, 课/課, …), so short Simplified asks
like「你有中文课吗？」falsely tied and defaulted to zh-HK.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Set, Tuple

# Explicit high-frequency pairs the bulk lists historically missed.
_EXTRA_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("吗", "嗎"),
    ("么", "麼"),
    ("课", "課"),
    ("对", "對"),
    ("语", "語"),
    ("读", "讀"),
    ("写", "寫"),  # also in bulk; safe
    ("请", "請"),
    ("现", "現"),
    ("钱", "錢"),
    ("贵", "貴"),
    ("费", "費"),
    ("报", "報"),
    ("页", "頁"),
    ("觉", "覺"),
    ("来", "來"),
    ("话", "話"),
    ("据", "據"),
    ("号", "號"),
    ("达", "達"),
    ("过", "過"),
    ("还", "還"),
    ("这", "這"),
    ("为", "為"),
    ("会", "會"),
    ("个", "個"),
    ("们", "們"),
    ("学", "學"),
    ("习", "習"),
    ("听", "聽"),
    ("说", "說"),
    ("时", "時"),
    ("长", "長"),
    ("书", "書"),
    ("门", "門"),
    ("问", "問"),
    ("开", "開"),
    ("关", "關"),
    ("车", "車"),
    ("东", "東"),
    ("买", "買"),
    ("卖", "賣"),
    ("点", "點"),
    ("电", "電"),
    ("网", "網"),
    ("后", "後"),
    ("发", "發"),
    ("见", "見"),
    ("国", "國"),
    ("从", "從"),
    ("爱", "愛"),
    ("儿", "兒"),
    ("两", "兩"),
    ("万", "萬"),
    ("与", "與"),
    ("乐", "樂"),
    ("区", "區"),
    ("医", "醫"),
    ("华", "華"),
    ("单", "單"),
    ("卖", "賣"),
    ("卫", "衛"),
    ("历", "歷"),
    ("压", "壓"),
    ("厅", "廳"),
    ("厉", "厲"),
    ("双", "雙"),
    ("发", "髮"),  # hair — second mapping; handled by set add
    ("叶", "葉"),
    ("叹", "嘆"),
    ("严", "嚴"),
    ("喽", "嘍"),
    ("吗", "嗎"),
)

# Shared glyphs that must never count as distinctive (even if a pair tries).
_SHARED_BAN: Set[str] = set(
    "的一是不了人我在有他上去大到以要就也可你生能而子那得于着下自之年成事"
    "面同方前心看没和她它我们您谁什么哪怎此其每很最已又也还只都"
    "家台干才托折才米目耳手足口日日月山水火土木金石"
)

# Legacy bulk (recall). Gaps filled by _EXTRA_PAIRS.
_LEGACY_SIMP = (
    "爱摆备笔边参仓产长车虫从电东风发丰复个关广国过华画汇会几夹监见荐将节尽进举据开"
    "乐离礼丽两灵龙楼录陆妈买卖门们难鸟农齐气迁亲穷区权让认赛杀师时识属双说丝肃"
    "岁孙态体条铁听厅头图团为卫稳问无务戏习系显献乡写兴选学寻压严业医义艺阴隐应营"
    "拥优邮鱼与云杂灾郑执质专种众钟筑庄装壮贝呗狈绷毙毕宾补惨灿蚕层搀谗馋缠忏偿厂"
    "彻尘衬称惩迟冲丑出处础处触辞聪丛担胆导灯邓敌籴递点淀冬斗独吨夺堕儿矾范飞坟"
    "奋粪凤肤妇盖赶巩沟构购谷顾刮观柜汉号轰胡壶沪护划怀坏欢环回伙获击"
    "鸡积极际继价艰歼拣硷舰姜浆桨奖讲酱胶阶疖洁借仅惊竞旧剧惧卷克垦恳夸块亏困"
    "腊蜡兰拦栏烂累垒类里隶帘联怜炼练粮疗辽猎临邻岭庐芦炉驴乱霉蒙梦庙"
    "蔑亩恼脑拟酿疟盘辟苹凭扑仆朴启签千牵纤窍窃寝庆琼秋曲劝确扰热洒伞丧扫涩"
    "晒伤舍沈声胜湿实适势兽书术树帅松苏虽随坛叹誊粜涂椭洼袜网雾牺虾吓咸显宪县响向协胁亵衅须悬选旋盐阳养痒样钥药爷叶亿忆"
    "佣踊忧余吁郁誉渊园远跃运酝赃脏凿枣灶斋毡战赵折这征症证只致制肿"
    "众昼朱烛桩妆准浊总钻"
)
_LEGACY_TRAD = (
    "愛罷備筆邊參倉產長車蟲從電東風髮發豐復複個關廣國過華畫匯彙會幾夾監見薦將節盡儘進"
    "舉據開樂離禮麗兩靈劉龍樓錄陸媽買賣門們難鳥農齊氣遷親窮區權讓認賽殺師時識屬雙說絲"
    "肅歲孫態體條鐵聽廳頭圖團為衛穩問無務戲習係繫顯獻鄉寫興選學尋壓嚴業醫義藝陰隱應營"
    "擁優郵魚與雲雜災鄭執質專種眾鐘鍾築莊裝壯貝唄狽綳繃斃畢賓補慘燦蠶層攙讒饞纏懺償廠"
    "徹塵襯稱懲遲沖衝醜齣礎處觸辭聰叢擔膽導燈鄧敵糴遞點澱鼕鬥獨噸奪墮兒礬範飛墳奮糞"
    "鳳膚婦蓋趕鞏溝構購穀顧颳觀櫃漢號閤轟後鬍壺滬護劃懷壞歡環還迴夥獲擊雞積極"
    "際繼傢價艱殲揀鹼艦薑漿槳獎講醬膠階癤潔藉僅驚競舊劇懼捲剋墾懇誇塊虧睏臘蠟蘭攔欄"
    "爛纍壘類裏禮隸簾聯憐煉練糧療遼瞭獵臨鄰嶺廬蘆爐陸驢亂麼黴濛懞矇夢麵廟衊畝惱腦擬釀"
    "瘧盤闢蘋憑撲僕樸啟簽籤韆牽縴竅竊寢慶瓊鞦麴權勸確讓擾熱認灑傘喪掃澀曬傷捨瀋聲勝濕"
    "實適勢獸書術樹帥鬆蘇雖隨臺檯颱態壇罎嘆謄體糶鐵聽廳頭圖塗團糰橢窪襪網衛穩務霧犧習"
    "係繫戲蝦嚇鹹顯憲縣響嚮協脅褻釁興鬚懸選鏇壓鹽陽養癢樣鑰藥爺葉醫億憶應擁傭踴憂優郵"
    "餘禦籲鬱譽淵園遠願躍運醞雜贓臟髒鑿棗竈齋氈戰趙摺這徵癥證隻衹緻製鐘鍾腫種眾晝硃燭"
    "築莊樁妝裝壯準濁總鑽"
)


def _build_variant_sets() -> Tuple[Set[str], Set[str]]:
    simp: Set[str] = set()
    trad: Set[str] = set()
    for s, t in _EXTRA_PAIRS:
        if not s or not t or s == t:
            continue
        if s in _SHARED_BAN or t in _SHARED_BAN:
            continue
        simp.add(s)
        trad.add(t)
    for ch in _LEGACY_SIMP:
        if ch not in _SHARED_BAN and ch not in trad:
            simp.add(ch)
    for ch in _LEGACY_TRAD:
        if ch not in _SHARED_BAN and ch not in simp:
            trad.add(ch)
    both = (simp & trad) | _SHARED_BAN
    simp -= both
    trad -= both
    return simp, trad


SIMP_ONLY, TRAD_ONLY = _build_variant_sets()


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text).lower()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _is_cjk(char: str) -> bool:
    o = ord(char)
    return (
        0x4E00 <= o <= 0x9FFF
        or 0x3400 <= o <= 0x4DBF
        or 0x20000 <= o <= 0x2A6DF
    )


def _char_counts(text: str) -> Dict[str, int]:
    counts = {
        "cjk": 0,
        "latin": 0,
        "trad_only": 0,
        "simp_only": 0,
        "digits": 0,
        "other": 0,
    }
    for char in text or "":
        if char.isspace() or not char.isprintable():
            continue
        if char.isdigit():
            counts["digits"] += 1
            continue
        if _is_cjk(char):
            counts["cjk"] += 1
            if char in TRAD_ONLY:
                counts["trad_only"] += 1
            elif char in SIMP_ONLY:
                counts["simp_only"] += 1
            continue
        if "a" <= char <= "z" or char.isalpha():
            counts["latin"] += 1
            continue
        counts["other"] += 1
    return counts


def _get_chinese_variant_scored(text: str) -> Tuple[str, Dict[str, Any]]:
    trad_score = 0
    simp_score = 0
    hits: List[str] = []
    for char in text:
        if char in TRAD_ONLY:
            trad_score += 1
            if len(hits) < 24:
                hits.append(f"{char}=trad")
        elif char in SIMP_ONLY:
            simp_score += 1
            if len(hits) < 24:
                hits.append(f"{char}=simp")
    detail: Dict[str, Any] = {
        "trad_only": trad_score,
        "simp_only": simp_score,
        "hits": hits,
    }
    if trad_score > simp_score:
        return "zh-HK", {**detail, "winner": "trad_only", "tie_break": None}
    if simp_score > trad_score:
        return "zh-CN", {**detail, "winner": "simp_only", "tie_break": None}
    return "zh-HK", {**detail, "winner": "tie", "tie_break": "default_zh-HK"}


def _prefer_variant_from_accept_language(header: Optional[str]) -> Optional[str]:
    if not header:
        return None
    h = header.lower()
    if "zh-hant" in h or "zh-tw" in h or "zh-hk" in h:
        return "zh-HK"
    if "zh-hans" in h or "zh-cn" in h or "zh-sg" in h:
        return "zh-CN"
    if "zh" in h:
        return "zh-HK"
    return None


CJK_THRESHOLD = 0.3


def explain_language_code(
    user_message: str,
    accept_language_header: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Same decision as get_language_code, plus inspector-friendly counts.

    Majority steps:
    1) Count CJK vs Latin letters → cjk_ratio.
    2) If ratio ≥ 0.3, count TRAD_ONLY vs SIMP_ONLY distinctive chars (majority wins;
       ties default zh-HK).
    3) Optional Accept-Language override for zh variant.
    """
    normalized_message = _normalize_text(user_message)
    counts = _char_counts(normalized_message)
    letter_count = counts["cjk"] + counts["latin"]
    cjk_ratio = (counts["cjk"] / letter_count) if letter_count else 0.0
    header_pref = _prefer_variant_from_accept_language(accept_language_header)
    base: Dict[str, Any] = {
        "counts": counts,
        "cjk_ratio": round(cjk_ratio, 4),
        "cjk_threshold": CJK_THRESHOLD,
        "accept_language": header_pref,
        "normalized_len": len(normalized_message),
    }

    if not normalized_message:
        decision = header_pref or "en"
        return {
            **base,
            "decision": decision,
            "method": "empty_default",
            "why": (
                f"Empty message · Accept-Language → {decision}"
                if header_pref
                else "Empty message · default en"
            ),
            "variant_counts": None,
        }

    common_english_greetings = {"hi", "hello", "thanks", "thank you", "ok", "yes", "no"}
    if normalized_message in common_english_greetings:
        return {
            **base,
            "decision": "en",
            "method": "greeting_fast_path",
            "why": f"Exact short English token «{normalized_message}»",
            "variant_counts": None,
        }

    if cjk_ratio >= CJK_THRESHOLD:
        variant, variant_counts = _get_chinese_variant_scored(normalized_message)
        if header_pref in ("zh-HK", "zh-CN"):
            return {
                **base,
                "decision": header_pref,
                "method": "accept_language_override",
                "why": (
                    f"CJK ratio {cjk_ratio:.2f} ≥ {CJK_THRESHOLD} · "
                    f"Accept-Language forces {header_pref}"
                ),
                "variant_counts": variant_counts,
                "script_guess": variant,
            }
        why = (
            f"CJK ratio {cjk_ratio:.2f} ≥ {CJK_THRESHOLD} · "
            f"trad {variant_counts['trad_only']} vs simp {variant_counts['simp_only']}"
        )
        if variant_counts.get("hits"):
            why += f" · {', '.join(variant_counts['hits'][:6])}"
        if variant_counts.get("tie_break"):
            why += " · tie → zh-HK"
        return {
            **base,
            "decision": variant,
            "method": "cjk_majority",
            "why": why,
            "variant_counts": variant_counts,
        }

    return {
        **base,
        "decision": "en",
        "method": "latin_majority",
        "why": f"CJK ratio {cjk_ratio:.2f} < {CJK_THRESHOLD} → en",
        "variant_counts": None,
    }


def get_language_code(
    user_message: str,
    accept_language_header: Optional[str] = None,
) -> str:
    """Return 'en' | 'zh-CN' | 'zh-HK' for a parent message."""
    return explain_language_code(user_message, accept_language_header)["decision"]
