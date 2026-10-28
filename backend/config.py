"""Environment-driven settings for BrightNest COMP6708 demo backend."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_BACKEND_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _BACKEND_DIR.parent


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

    # Generation
    gen_max_tokens: int = int(os.environ.get("GEN_MAX_TOKENS", "500"))
    gen_temperature: float = float(os.environ.get("GEN_TEMPERATURE", "0.3"))

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
