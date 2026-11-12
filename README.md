# BrightNest Learning Centre — COMP6708 Assignment 3 Demo

Standalone LLM assistant for a **fictional** Hong Kong bilingual kids' learning centre (**BrightNest / 亮巢學習中心**).

Built for COMP6708 Advanced Big Data Computing — Mini Demo (live system, RAG-first).

> This repo is isolated from the production Little Scholars tutoring deployment. Do not point this code at live WhatsApp/YCloud/AWS production resources.

**New to the repo?** Read **[docs/TEAM.md](docs/TEAM.md)** end-to-end — full system document (router, RAG channel pack, tools, inspector, deploy, demo).

**Live demo:** https://seanfontaine.dev/COMP6708_A3_Demo · API on Alibaba SAE (`brightnest-api:v68+`)

## What's in the box

| Path | Role |
|------|------|
| `backend/` | FastAPI: hybrid router + RAG + booking/cancel FSM + staff desk |
| `content/` | Markdown KB (en / zh-HK / zh-CN) — programmes, fees, age_bands, policies, … |
| `integration/developer-site/` | Drop-ins for the live UI on seanfontaine.dev |
| `deploy/` | Alibaba Docker + SAE redeploy |
| `docs/` | TEAM guide, demo script, architecture |
| `slides/` | Beamer / Overleaf slide source |
| `frontend/` | Optional standalone Vite chat (prefer developer-site host for the graded demo) |

## Techniques demonstrated (show these live)

1. **Training-free RAG** — chunk Markdown → DashScope embeddings → Chroma ∥ in-process BM25 → **channel pack (3 dense + 2 BM25)** → Qwen + citations
2. **Tool registry + cascade** — OpenAI-style tool schemas; L1 detectors (&lt;1ms) then L2 cheap action_policy on ambiguous RAG — **not** LLM-first tool-calling every turn. Inspector groups: **Tool / dialogue**, **Live info**, **RAG default**
3. **Live info paths** — opening hours calendar / typhoon+HKO with privileged live docs + KB enrich
4. **Tool FSMs** — sticky book/cancel with cheap **DST JSON**, **final check** before commits, assessment → SheetPro
5. **Staff escalation** — Final check → Staff email draft → MailBox (safety fail-open)

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

First request (or startup) builds the Chroma + BM25 indexes from `../content`.

### Graded UI (developer-site)

```powershell
cd ..\developer-site\frontend\tailwindcss4
npm install
npm run dev
```

Open the BrightNest demo route — API defaults to same-origin `/brightnest-api` (Vite proxy → local uvicorn or SAE).

## API surface (high-signal)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/chat` | Router → FSM / live / RAG (+ inspector `trace`) |
| `POST` | `/admin/reindex` | Rebuild dense + sparse indexes |
| `GET` | `/admin/messages` | Staff escalation inbox |
| `GET` | `/healthz` | Health |

## Alibaba Cloud

Setup: [deploy/alibaba-cloud.md](deploy/alibaba-cloud.md).  
Redeploy: [deploy/REDEPLOY.md](deploy/REDEPLOY.md) (`.\deploy\redeploy.ps1 -Tag vN`).  
**Stop the SAE app before every new image deploy.**

## Demo day

Talk track: [docs/demo-script.md](demo-script.md) · System truth: [docs/TEAM.md](docs/TEAM.md) · Slides: [slides/](slides/).

## Host UI on seanfontaine.dev

Frontend lives at **`/COMP6708_A3_Demo`**. Browser uses `/brightnest-api` (Vercel rewrite → SAE) to avoid mixed-content blocks.

- Team / handoff: [docs/TEAM.md](docs/TEAM.md), [AGENT_HANDOFF.md](AGENT_HANDOFF.md)
- Drop-in files: [integration/developer-site/](integration/developer-site/)
