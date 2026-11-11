"""Environment-driven settings for BrightNest COMP6708 demo backend."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_BACKEND_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _BACKEND_DIR.parent

# Windows editors sometimes save .env as cp1252 (em-dash 0x97 in comments).
_env_path = _REPO_ROOT / ".env"
if _env_path.is_file():
    for _enc in ("utf-8", "utf-8-sig", "cp1252"):
        try:
            load_dotenv(_env_path, encoding=_enc)
            break
        except UnicodeDecodeError:
            continue
else:
    load_dotenv()


@dataclass
class Settings:
    # Paths
    content_dir: Path = Path(os.environ.get("CONTENT_DIR", str(_REPO_ROOT / "content")))
    chroma_dir: Path = Path(os.environ.get("CHROMA_DIR", str(_BACKEND_DIR / "data" / "chroma")))
    bookings_db: Path = Path(os.environ.get("BOOKINGS_DB", str(_BACKEND_DIR / "data" / "bookings.db")))
    schedule_sheets_dir: Path = Path(
        os.environ.get("SCHEDULE_SHEETS_DIR", str(_BACKEND_DIR / "data" / "sheets"))
    )
    chat_history_db: Path = Path(
        os.environ.get("CHAT_HISTORY_DB", str(_BACKEND_DIR / "data" / "chat_history.db"))
    )

    # DashScope / Qwen (Alibaba)
    dashscope_api_key: str = os.environ.get("DASHSCOPE_API_KEY", "")
    # Native DashScope HTTP root (.../api/v1), NOT compatible-mode
    dashscope_base_url: str = os.environ.get(
        "DASHSCOPE_BASE_URL",
        "https://dashscope.aliyuncs.com/api/v1",
    )
    llm_model: str = os.environ.get("LLM_MODEL", "qwen-plus")
    embedding_model: str = os.environ.get("EMBEDDING_MODEL", "text-embedding-v4")


    # RAG
    rag_top_k: int = int(os.environ.get("RAG_TOP_K", "5"))
    rag_chunk_size: int = int(os.environ.get("RAG_CHUNK_SIZE", "900"))
    rag_chunk_overlap: int = int(os.environ.get("RAG_CHUNK_OVERLAP", "120"))
    collection_name: str = os.environ.get("CHROMA_COLLECTION", "brightnest_kb")
    # Hybrid retrieve: dense (Chroma) + sparse (in-process BM25) fused with RRF
    rag_hybrid_enabled: bool = os.environ.get("RAG_HYBRID_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    rag_rrf_k: int = int(os.environ.get("RAG_RRF_K", "60"))
    # Keep the hybrid pool small — KB is ~hundreds of chunks per language
    rag_sparse_pool_k: int = int(os.environ.get("RAG_SPARSE_POOL_K", "5"))

    # Generation
    # Keep short for live demo latency (thinking is also forced off in generation_parameters).
    gen_max_tokens: int = int(os.environ.get("GEN_MAX_TOKENS", "280"))
    gen_temperature: float = float(os.environ.get("GEN_TEMPERATURE", "0.3"))

    # Cheap DashScope demo arbiters (turbo). Master switch + per-use toggles.
    intent_arbiter_enabled: bool = os.environ.get("INTENT_ARBITER_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    # Empty INTENT_ARBITER_MODEL → reuse LLM_MODEL (SAE MaaS often only exposes one).
    intent_arbiter_model: str = (
        os.environ.get("INTENT_ARBITER_MODEL")
        or os.environ.get("LLM_MODEL", "qwen-turbo")
    )
    intent_arbiter_timeout_s: float = float(os.environ.get("INTENT_ARBITER_TIMEOUT_S", "12"))
    # Extra demo polish paths (still gated by intent_arbiter_enabled + DashScope key)
    arbiter_adapt_extra: bool = os.environ.get("ARBITER_ADAPT_EXTRA", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    arbiter_gap_rewrite: bool = os.environ.get("ARBITER_GAP_REWRITE", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    arbiter_cancel_pick: bool = os.environ.get("ARBITER_CANCEL_PICK", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    arbiter_booking_parse: bool = os.environ.get("ARBITER_BOOKING_PARSE", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    # CRAG-lite corrective rewrite: only after weak first retrieve + prior chat
    arbiter_query_rewrite: bool = os.environ.get("ARBITER_QUERY_REWRITE", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    # Final cheap-LLM gate before book / cancel / escalate / refuse commits
    arbiter_final_check: bool = os.environ.get("ARBITER_FINAL_CHECK", "true").lower() in (
        "1",
        "true",
        "yes",
    )

    # Soft structured routes: retrieve KB chunks, then compose with the fact card
    # as privileged context (allergy, fees, teachers, …). Hard tools stay bypass.
    grounding_enrich_enabled: bool = os.environ.get(
        "GROUNDING_ENRICH_ENABLED", "true"
    ).lower() in ("1", "true", "yes")
    grounding_enrich_top_k: int = int(os.environ.get("GROUNDING_ENRICH_TOP_K", "3"))

    # Multi-turn memory (~5 user/bot turns ≈ 10 messages)
    chat_history_enabled: bool = os.environ.get("CHAT_HISTORY_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    chat_history_limit: int = int(os.environ.get("CHAT_HISTORY_LIMIT", "10"))

    # Opening hours / HKO weather / HK holidays
    opening_hours_enabled: bool = os.environ.get("OPENING_HOURS_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    opening_hours_weather_enabled: bool = os.environ.get(
        "OPENING_HOURS_WEATHER_ENABLED", "true"
    ).lower() in ("1", "true", "yes")

    # CORS — comma-separated origins; * for local demo
    cors_origins: str = os.environ.get("CORS_ORIGINS", "*")

    # Brand
    brand_name: str = "BrightNest Learning Centre"
    brand_name_zh: str = "亮巢學習中心"

    @property
    def has_dashscope(self) -> bool:
        return bool(self.dashscope_api_key.strip())


SETTINGS = Settings()
