# BrightNest Learning Centre — COMP6708 Assignment 3 Demo

Standalone LLM assistant for a **fictional** Hong Kong bilingual kids' learning centre (**BrightNest / 亮巢學習中心**).

Built for COMP6708 Advanced Big Data Computing — Mini Demo (live system, RAG-first).

> This repo is isolated from the production Little Scholars tutoring deployment. Do not point this code at live WhatsApp/YCloud/AWS production resources.

**New to the repo?** Start with **[docs/TEAM.md](docs/TEAM.md)** (run locally, edit KB, Admin panel, deploy, demo day).

## What's in the box

| Path | Role |
|------|------|
| `frontend/` | Vite + React chat UI (citations, availability, booking, Admin) |
| `backend/` | FastAPI: self-built RAG + tools + staff escalation |
| `content/` | Markdown knowledge base (en / zh-HK / zh-CN) |
| `deploy/` | Alibaba Cloud Docker + SAE redeploy |
| `docs/` | Team guide, demo script, architecture, slides outline |
| `slides/` | Beamer / Overleaf slide source |
| `integration/developer-site/` | Drop-ins for hosting the UI on seanfontaine.dev |

### Techniques demonstrated

1. **Retrieval-Augmented Generation (RAG)** — own chunk → embed → Chroma retrieve → generate pipeline
2. **DashScope / Qwen** (Alibaba) for embeddings + chat when `DASHSCOPE_API_KEY` is set
3. **Tool-style booking** — availability + SQLite bookings surfaced in the UI
4. **Staff escalation** — human hand-off path (angry / ask for person / safety) → Admin Messages inbox

Offline fallback: deterministic hash embeddings + extractive answers (no cloud key required).

## Quick start (local)

### Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# optional: copy .env.example → .env and add DASHSCOPE_API_KEY
uvicorn main:app --reload --port 8000
```

First request (or startup) builds the Chroma index from `../content`.

### Frontend

```powershell
cd frontend
npm install
copy .env.example .env
npm run dev
```

Open http://localhost:5173 — API defaults to same-origin `/brightnest-api` (Vite proxy → local uvicorn or SAE). See `frontend/.env.example`.

## API surface

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/chat` | RAG / hours / availability / escalation |
| `GET` | `/courses` | Course catalogue |
| `GET` | `/availability` | Open seats |
| `POST` | `/bookings` | Create provisional booking |
| `GET` | `/admin/messages` | Staff escalation inbox |
| `POST` | `/admin/messages/{id}/ack` | Acknowledge a staff message |
| `POST` | `/admin/reindex` | Rebuild vector index |
| `GET` | `/healthz` | Health |

## Alibaba Cloud

Setup: [deploy/alibaba-cloud.md](deploy/alibaba-cloud.md).  
Redeploy: [deploy/REDEPLOY.md](deploy/REDEPLOY.md) (`.\deploy\redeploy.ps1 -Tag vN`).  
**Stop the SAE app before every new image deploy.**

## Demo day

See [docs/demo-script.md](docs/demo-script.md) and [docs/slides-outline.md](docs/slides-outline.md).

## Host UI on seanfontaine.dev

Frontend is intended to live at **`/COMP6708_A3_Demo`** on the developer site (not OSS). Browser uses `/brightnest-api` (Vercel rewrite → SAE) to avoid mixed-content blocks.

- Team / handoff: [docs/TEAM.md](docs/TEAM.md), [AGENT_HANDOFF.md](AGENT_HANDOFF.md)
- Full steps: [docs/DEVELOPER_SITE_INTEGRATION.md](docs/DEVELOPER_SITE_INTEGRATION.md)
- Drop-in files: [integration/developer-site/](integration/developer-site/)
