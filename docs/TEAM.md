# Team guide — BrightNest COMP6708 demo

Short orientation for group members. Brand is **BrightNest / 亮巢** (fictional). Do not point this stack at live WhatsApp / YCloud / production tutoring.

## Who does what in the repo

| Area | Path | Notes |
|------|------|--------|
| Chat UI | `frontend/` | Vite + React; citations, slots, booking, Admin panel |
| Hosted UI copy | `integration/developer-site/` | Drop-ins for seanfontaine.dev `/COMP6708_A3_Demo` |
| API | `backend/` | FastAPI: RAG, hours, availability, bookings, admin |
| Knowledge base | `content/{en,zh-HK,zh-CN}/` | Markdown with YAML frontmatter → Chroma |
| Deploy | `deploy/` | Docker image → Alibaba SAE |
| Slides | `slides/` + `docs/slides-outline.md` | Beamer source + outline |
| Live demo script | `docs/demo-script.md` | ≤ 5 minutes |

## Run locally (Windows)

**Backend** (terminal 1):

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # then set DASHSCOPE_API_KEY if you have one
uvicorn main:app --reload --port 8000
```

**Frontend** (terminal 2):

```powershell
cd frontend
npm install
copy .env.example .env   # defaults to /brightnest-api via Vite proxy
npm run dev
```

Open http://localhost:5173. The Vite proxy forwards `/brightnest-api` → local uvicorn (`BRIGHTNEST_API_PROXY_TARGET`) or SAE.

Without a DashScope key the bot still runs (hash embeddings + extractive answers).

## How a chat request is routed

1. **Staff escalation** — parent asks for a human, is angry, or raises safety → calm parent reply + row in **Admin → Messages** (`staff_messages.csv`). Never mention internal sheets to parents.
2. **Opening hours / availability / booking tools** — structured answers + UI cards.
3. **RAG** — retrieve markdown chunks → generate (Qwen if keyed). Soft gaps can land in Admin to-dos for research; that is separate from staff Messages.

Reindex after KB edits: Admin UI, or `POST /admin/reindex`.

## Editing the knowledge base

1. Add/edit a file under `content/en/...` (and mirror `zh-HK` / `zh-CN` when the topic is parent-facing).
2. Keep frontmatter (`language`, `type`, `canonical`, `aliases`).
3. Restart or call **Reindex**. First chat after boot also builds the index if empty.

Good escalation policy doc: `content/en/faq/staff_escalation.md`.

## Admin panel (demo UI)

In the chat app, open **Admin**:

| Tab | Purpose |
|-----|---------|
| To-dos | Soft KB gaps / follow-up research flags |
| Messages | Direct bot → staff notices (escalation) |
| Bookings / calendar | Provisional bookings |

**Reset** clears chat and can clear admin sheets — use before a clean rehearsal.

## Deploy (API on Alibaba SAE)

**Always stop the app before deploying a new image**, or SAE can stick mid change-order.

```powershell
.\deploy\redeploy.ps1 -Tag vN
```

Details: [REDEPLOY.md](../deploy/REDEPLOY.md) and [alibaba-cloud.md](../deploy/alibaba-cloud.md).

Frontend on seanfontaine.dev: push `developer-site` (Vercel). Browser calls same-origin `/brightnest-api` (rewrite → SAE) so HTTPS pages are not blocked by mixed content.

## Demo day

1. Rehearse [demo-script.md](demo-script.md).
2. Confirm `GET /healthz` and one booking + one RAG answer.
3. Optional show: angry / “speak to a manager” → Admin **Messages**.
4. Slides support only — operate the live system.

## Secrets

- Never commit `.env` (gitignored). Only `*.env.example` with placeholders.
- Do not paste DashScope keys into chat, issues, or slides.

## Useful links inside the repo

- Architecture diagram: [architecture.md](architecture.md)
- Submission packaging: [submission-checklist.md](submission-checklist.md)
- Host UI integration: [DEVELOPER_SITE_INTEGRATION.md](DEVELOPER_SITE_INTEGRATION.md)
