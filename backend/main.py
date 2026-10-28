"""BrightNest COMP6708 A3 — chatbot-only FastAPI entrypoint."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import SETTINGS
from router import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Build / load vector index on startup (local hash or DashScope)
    try:
        from rag.pipeline import ensure_index

        ensure_index()
        print("[STARTUP] RAG index ready.", flush=True)
    except Exception as e:
        print(f"[STARTUP] RAG index deferred: {e}", flush=True)
    yield


app = FastAPI(
    title="BrightNest Learning Centre API",
    description="COMP6708 A3 demo — self-built RAG chatbot with booking tools",
    version="1.0.0",
    lifespan=lifespan,
)

origins = [o.strip() for o in SETTINGS.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/")
def root():
    return {
        "status": "ok",
        "brand": SETTINGS.brand_name,
        "brand_zh": SETTINGS.brand_name_zh,
        "rag": "dashscope" if SETTINGS.has_dashscope else "local_fallback",
    }


@app.get("/healthz")
def healthz():
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
