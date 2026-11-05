# BrightNest — end-to-end system guide (COMP6708 · Group 15)

| Member | Name | Student ID |
|--------|------|------------|
| 1 | FONTAINE Sean Hsien-ming | 26071164r |
| 2 | JIANG Junwei | 26107939r |
| 3 | MAO Shiwei | 25038479r |
| 4 | YAN Xingzhuo | 25013043r |

**Course:** COMP6708 Advanced Big Data Computing  
**Demo:** 25 November 2026 · 5-minute **live** system demo  
**Live UI:** https://seanfontaine.dev/COMP6708_A3_Demo  
**API:** Alibaba SAE (`/brightnest-api` via Vercel rewrite)  
**Slides:** `slides/main.tex` → `Assignment3-Group15.pdf`  
**Script:** [demo-script.md](demo-script.md)

Brand is **BrightNest / 亮巢** (fictional). Do **not** point this stack at live WhatsApp / production tutoring.

This document is the source of truth for **how the system actually works**, not a roster page. Read it before changing routing, RAG, or the demo UI.

---

## 1. What problem we solve

A bilingual Hong Kong kids' learning centre gets the **same parent questions after hours**:

- Are you open during **Signal 8** / Christmas?
- How much is **phonics**?
- How many **seats** are left Saturday?
- Do you offer **private 1:1**?
- Can I **book / cancel** for my child?
- My child had an **allergy reaction** — call me.

Staff cannot live on WhatsApp. A wrong fee or hours answer costs trust. BrightNest is the assistant we would ship: **answer from centre truth**, **act with tools**, **hand off when a human must**.

---

## 2. Runtime topology (what talks to what)

```text
Parent browser
  └─ React demo UI  (seanfontaine.dev/COMP6708_A3_Demo)
       │  HTTPS same-origin
       ▼
     /brightnest-api/*   (Vercel rewrite)
       │
       ▼
     FastAPI on Alibaba SAE  (EIP, Docker image brightnest-api:vN)
       ├─ Hybrid intent router          backend/services/intent_router.py
       ├─ Structured centre facts       backend/services/centre_facts.py
       ├─ Opening-hours engine          backend/llm/opening_hours.py
       ├─ Booking / cancel dialogues    backend/services/booking_*.py
       ├─ Staff escalation + email      backend/services/escalation.py, staff_*
       ├─ Admin follow-up sheet         backend/services/admin_todos.py
       ├─ RAG pipeline                  backend/rag/*
       │    ├─ ingest → Chroma          content/**/*.md
       │    ├─ embed (DashScope)        text-embedding-v*
       │    └─ generate (Qwen)          SETTINGS.llm_model
       └─ Session memory (SQLite)       backend/data/chat_history.db
```

**Frontend hosting:** Vercel (`developer-site` repo).  
**Backend hosting:** SAE only — never deploy a new image while the app is still RUNNING (see `deploy/REDEPLOY.md`).

---

## 3. One chat turn — exact path

Every `POST /chat` does the same skeleton (`backend/router.py::chat`):

1. **Language detect** — `en` / `zh-HK` / `zh-CN` from the message (overridable).
2. **Session memory load** — last `CHAT_HISTORY_LIMIT` (default 10) turns from SQLite, keyed by `session_id`.
3. **Hybrid intent router** — scores rules → priority pick → optional cheap LLM review.
4. **Branch on `route`:**
   - Structured / tool routes → fetch fact or run dialogue → optional **main LLM fact rewrite** (`adapt`) → **RAG skipped**.
   - `rag` → retrieve top-k chunks → generate → citations + RAG debug.
   - `staff_escalation` → parent ack + staff inbox row (+ optional Gmail draft).
   - `unknown_programme` / soft KB gap → parent-safe refuse + **follow-up sheet** row.
5. **Persist** parent + bot turns into session memory.
6. **Return** `answer`, `citations`, `slots?`, `trace` (full inspector payload).

The live **System inspector** reads `trace` only. If something is not in `trace`, the UI cannot show it.

### 3.1 What `trace` contains (inspector contract)

| Field | Meaning |
|-------|---------|
| `route` | Final route name after arbiter |
| `pipeline_steps[]` | Ordered chips: lang → history → intent → arbiter → facts/adapt/rag… |
| `routing.scores` | Intent scores after cleaning |
| `routing.rules_evaluated[]` | **Every** named rule: fired / idle / cleared by conflict |
| `routing.decision_path[]` | Human path: rules → conflicts → pick → arbiter → final |
| `routing.winning_rule` | Score, pre-arbiter pick, arbiter called?, skip detail, override |
| `adapt` | Whether main LLM rewrote a structured fact, and **why** |
| `rag` | `index_size`, `top_k`, `hit_count`, `top_score`, `hits[]` with **full `text`**, `rank`, `score` |
| `history.turns[]` | Full prior turns (not a 600-char clip) |
| `llm` | Main vs cheap model, what each was used for |
| `latency_ms` | Wall time for the turn |

---

## 4. Hybrid intent router (the decision engine)

**File:** `backend/services/intent_router.py`

### 4.1 Design idea

Parents mix topics. A pure LLM classifier is slow and flaky for **typhoon / fees / seats**. A pure regex router misses paraphrase. We use both:

1. **Hard / structured regex rules** (centre_facts + dialogue detectors) assign high scores (0.85–1.0).
2. **Conflict resolution** clears lower-priority scores (e.g. `weather>hours`, `payment>fees`).
3. **Soft prototype classifier** blends only when no hard veto (≥0.98) is present.
4. **Priority pick** (`_PRIORITY`) breaks ties.
5. **Cheap DashScope arbiter** (`intent_arbiter`) may keep / reroute — **unless** high-confidence skip.

### 4.2 High-confidence arbiter skip

If `top_score ≥ 0.9`, there are **no material conflicts**, and the pick is **not** `{confused, off_domain, rag}`, the cheap LLM is **not called**:

```text
reason = high_confidence_skip
skip_detail = "top_score=1.00 ≥ 0.9, no material conflicts, route=severe_weather is structured"
```

**Demo implication:** Typhoon Signal 8 → `severe_weather` score 1.0 → arbiter skipped → RAG skipped → structured policy + fact rewrite. That is **correct**, not a bug. The inspector must say so.

### 4.3 Rule catalogue (what can fire)

| Rule id | What it catches | Typical score | Downstream |
|---------|-----------------|---------------|------------|
| `staff_escalation` | ask human / anger / safety | 1.0 | Staff inbox + email draft |
| `off_domain` | Tokyo weather, politics, crypto… | 0.98 | Refuse, **no** fake todo |
| `severe_weather` | T8 / Black Rain / typhoon policy | 1.0 | `centre_facts` + adapt |
| `fees` / `payment` / `location` / … | Published ops facts | 0.9–0.95 | Structured JSON/MD facts + adapt |
| `booking_tool` | seats left / availability | 0.85–0.95 | CSV register → seat cards |
| `booking_dialogue` / `cancel_dialogue` | book / cancel flows | ~0.85–0.95 | Sticky multi-turn state |
| `unknown_programme` | scuba / programmes we don't offer | 0.95 | Honest refuse + sheet row |
| `opening_hours` | hours / Christmas / “tomorrow?” | 0.8 | Deterministic calendar engine |
| `rag` | default / multi-topic / KB explainers | 0.5–0.65 | Chroma retrieve → generate |

**Important demo gotcha:**  
“How much is phonics?” → **`fees`** (structured fee guide), **not** RAG.  
“Do you offer private 1:1 tutoring?” → **`rag`**.  
Use the **RAG · Private 1:1** probe when you want to show chunks / scores / expand.

### 4.4 Why each decision is visible

After every turn the UI **Router decision** panel shows:

- Final route + winning score
- Pre-arbiter pick vs post-arbiter
- Cheap LLM: called or skipped (**with skip_detail**)
- Fact rewrite: ran? why?
- RAG: ran or skipped (and which route won instead)
- **Exact path** (`decision_path`)
- **Rule map** — every rule fired / idle / cleared
- Raw scores

If a rule did **not** fire, you can open the map and see it marked **idle**. If it fired but lost a conflict, it shows **cleared**.

---

## 5. Structured tools (when we refuse to “guess”)

### 5.1 Severe weather / hours / holidays

- **Weather policy** (`severe_weather`): canned centre policy from `centre_facts`, then optional Qwen **fact rewrite** for fluency (`adapt_structured_fact`). Facts must not change; tone may.
- **Opening hours** (`opening_hours`): parse date → holiday table → Sunday closed → HKO hint when “today”. Deterministic; not RAG.

### 5.2 Fees and other centre facts

Loaded from `backend/data/facts/*.json` (and related helpers). Same adapt path as weather.

### 5.3 Live seats + booking

- Admin register = local CSVs under `backend/data/sheets/` (teachers, windows, sessions, enrolments, trial_slots).
- “How many seats left in Saturday phonics?” → `booking_tool` → `seats_left` from enrolments, **not** from the LLM.
- Book writes `chat_bookings.csv` (provisional; no payment).
- Cancel / book dialogues keep **sticky session state** until completed or yielded.

### 5.4 Staff desk

Two surfaces, one backend:

1. **Follow-up sheet** (`admin_todos`) — soft gaps / unknown programmes. Quiet research queue.
2. **Gmail-style inbox** (`staff_messages`) — escalation. Urgent safety gets a drafted email body (call now, names, quote).

Parents never see sheet/inbox jargon in the chat reply.

---

## 6. RAG pipeline (self-built, training-free)

**Why RAG (assignment fit):** centre docs change weekly → **no fine-tune**. We implement chunk → embed → retrieve → generate ourselves so the technique is auditable in the inspector.

### 6.1 Knowledge base

- Source: `content/{en,zh-HK,zh-CN}/**/*.md`
- Ingest: `backend/rag/ingest.py` — frontmatter + body, ~900-char chunks with overlap
- Store: Chroma (persistent under backend data)
- Embeddings: DashScope when `DASHSCOPE_API_KEY` set; else deterministic local hash (offline demo)

### 6.2 Retrieve → generate

`backend/rag/pipeline.py::answer_question`:

1. Embed query.
2. Chroma top pool (wider pool when age digits present).
3. Optional age-FAQ overlay + **lexical rerank** (path/alias boosts).
4. Keep `SETTINGS.rag_top_k` (default 5).
5. Build prompt with numbered excerpts + session history context.
6. Generate with Qwen (`enable_thinking: false` on compatible endpoints) or extractive fallback.
7. Attach citations: `rank`, `score`, `snippet`, **full `text`**, `source`, `title`.

### 6.3 Gap handling

Weak / off-topic / `NO_ANSWER` / soft hand-off → may create an **admin todo** while still giving the parent a careful reply. Off-domain refuse does **not** create todos.

### 6.4 Inspector expectations

For a RAG turn you must be able to:

- See **index size**, **top_k**, **hit count**, **top score**, embedding mode
- Click each hit to **expand the full chunk**
- See **rank #** and **retrieval score** on every hit

If RAG was skipped, the panel must say **which route won** and point you at Router decision.

---

## 7. Session memory

- SQLite: `CHAT_HISTORY_DB` (default `backend/data/chat_history.db`)
- Limit: `CHAT_HISTORY_LIMIT` (default 10 messages)
- Fed into: RAG prompts, fact rewrite, hours follow-ups (“tomorrow?”), arbiter preview
- Inspector: expandable **per-turn** list (full text), plus optional raw context string

Truncation in the old UI was a display bug (600-char preview + CSS clamp). The API now returns `history.turns[]` in full for the loaded window.

---

## 8. Models & configuration

| Role | Typical setting | When used |
|------|-----------------|-----------|
| Main LLM | `LLM_MODEL` (e.g. `qwen3.8-flash`) | RAG generate, fact rewrite |
| Cheap arbiter | `INTENT_ARBITER_MODEL` | Intent review, gap polish, cancel pick, booking parse |
| Embeddings | `EMBEDDING_MODEL` | Ingest + query |

Feature flags (see `backend/config.py`): `INTENT_ARBITER_ENABLED`, `ARBITER_ADAPT_EXTRA`, `ARBITER_GAP_REWRITE`, `CHAT_HISTORY_ENABLED`, `OPENING_HOURS_ENABLED`.

DashScope calls that support it pass **`enable_thinking: false`** so latency stays demo-safe.

---

## 9. Demo UI map

Left: parent chat + probes + bookable cards.  
Right tabs:

- **System** — route pill, step rail, **Router decision**, hours/weather, **RAG retrieval**, class register, booking tool, session memory, probes
- **Staff desk** — Follow-up sheet | Inbox

Probes that matter for the 5-minute talk:

| Probe | Expected route | What to point at |
|-------|----------------|------------------|
| Typhoon | `severe_weather` | Router: rule fired, arbiter skipped, RAG skipped, adapt ran |
| Phonics fee | `fees` | Structured fact (not RAG) |
| RAG · Private 1:1 | `rag` | Expand chunks, scores, index/top_k |
| Phonics seats | `booking_tool` | Register + seat cards |
| Unknown ask | `unknown_programme` | Follow-up sheet row |
| Safety call | `staff_escalation` | Inbox urgent mail |

---

## 10. Local run

### Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# copy ../.env.example → ../.env (DASHSCOPE_API_KEY, etc.)
uvicorn main:app --reload --port 8000
```

### Frontend (developer-site)

```powershell
cd ..\developer-site\frontend\tailwindcss4
npm install
npm run dev
```

Production path is `/COMP6708_A3_Demo` with `/brightnest-api` proxy. Integration drop-ins live in `integration/developer-site/` (keep mirrored when editing the live app).

---

## 11. Deploy

**Backend (SAE):**

```powershell
.\deploy\redeploy.ps1 -Tag v22
```

Always: abort stuck change-order → **STOP** → build/push → deploy → smoke (`healthz`, reindex, chat). See `deploy/REDEPLOY.md`.

**Frontend:** push `developer-site` `master` (Vercel). Mirror JSX/CSS into `COMP6708_A3_Demo/integration/developer-site` in the same change set.

---

## 12. Weaknesses found while documenting (and what we did)

| Weakness | Risk | Fix |
|----------|------|-----|
| RAG hits only returned 280-char `snippet` | Demo cannot prove retrieval | API now includes full `text` + `rank` + `char_count` |
| UI CSS `-webkit-line-clamp: 2` with no expand | “Can't click chunks” | Clickable `RagHit` expand/collapse |
| Typhoon probe expected RAG | Misleading narrative | Router panel explains skip; dedicated **RAG · Private 1:1** probe |
| Phonics fee labelled as RAG in script | Factually wrong (`fees` tool) | Script rewritten: fees = structured; Private 1:1 = RAG |
| Intent “why” buried in nested debug | Markers cannot audit decisions | `rules_evaluated`, `decision_path`, `winning_rule`, `adapt` on `trace` |
| Session memory preview truncated to 600 chars + short `<pre>` | Looks broken mid-demo | `history.turns[]` + expandable turns UI |
| Arbiter skip opaque | Looks like “LLM didn't run” randomly | `skip_detail` string with thresholds |

---

## 13. Tests worth running before demo day

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
pytest tests/test_intent_routing.py tests/test_age_retrieval.py -q
```

Manual smoke against live EIP:

1. Typhoon → `severe_weather`, adapt done, rag skipped  
2. Private 1:1 → `rag`, hits with scores, expand full text  
3. Saturday phonics seats → cards with `seats_left`  
4. Scuba → sheet row  
5. Safety allergy → inbox urgent  

---

## 14. Related docs

- [architecture.md](architecture.md) — compact diagram  
- [demo-script.md](demo-script.md) — minute-by-minute talk track  
- [DEVELOPER_SITE_INTEGRATION.md](DEVELOPER_SITE_INTEGRATION.md) — Vercel wiring  
- [../deploy/REDEPLOY.md](../deploy/REDEPLOY.md) — SAE redeploy ritual  
- [../README.md](../README.md) — quick start entry point  
