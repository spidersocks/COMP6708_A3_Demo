# BrightNest — system architecture (COMP6708 · Group 15)

| Member | Name | Student ID |
|--------|------|------------|
| 1 | FONTAINE Sean Hsien-ming | 26071164r |
| 2 | JIANG Junwei | 26107939r |
| 3 | MAO Shiwei | 25038479r |
| 4 | YAN Xingzhuo | 25013043r |

**Live:** https://seanfontaine.dev/COMP6708_A3_Demo · **API:** Alibaba SAE via `/brightnest-api`  
**Code:** FastAPI (`backend/`) · React inspector (`developer-site` BrightNestBot) · KB `content/`

Fictional brand **BrightNest / 亮巢**. This document describes runtime behaviour end-to-end.

---

## 1. Deployment topology

```text
Browser (HTTPS)
  └─ React SPA  /COMP6708_A3_Demo
       │  same-origin fetch
       ▼
     Vercel rewrite  /brightnest-api/*  →  http://<SAE-EIP>:8000/*
       │
       ▼
     FastAPI  (Docker image brightnest-api:vN on Alibaba SAE)
       │
       ├─ services/intent_router.py     hybrid intent scores + arbiter gate
       ├─ services/centre_facts.py      structured JSON facts + regex intents
       ├─ llm/opening_hours.py          deterministic calendar / holidays / HKO
       ├─ services/booking_*.py         sticky book/cancel + CSV register
       ├─ services/escalation.py        safety / anger / ask-human
       ├─ services/staff_*.py           inbox + optional mail draft
       ├─ services/admin_todos.py       soft-gap follow-up sheet
       ├─ rag/{ingest,pipeline,generator,store,embeddings}.py
       │     content/{en,zh-HK,zh-CN}/**/*.md  →  Chroma  →  Qwen
       └─ llm/chat_history.py           SQLite session memory
```

| Tier | Host | Artifact |
|------|------|----------|
| UI | Vercel (`developer-site`) | Vite React |
| API | SAE `cn-hongkong` | `deploy/Dockerfile.backend` → ACR → DeployApplication |
| Models | DashScope (HK / CN endpoint via `DASHSCOPE_BASE_URL`) | chat + embeddings |

Redeploy ritual: abort change-order → **StopApplication** → build/push tag → DeployApplication → `GET /healthz` → `POST /admin/reindex`. See `deploy/REDEPLOY.md`.

---

## 2. Request lifecycle (`POST /chat`)

Entry: `backend/router.py::chat`.

| Step | Module | Output on `trace` |
|------|--------|-------------------|
| 1. Language | `get_language_code` | `language` |
| 2. History load | `get_recent_history(session_id, limit)` | `history.turns[]`, `turns_loaded`, `limit` |
| 3. Intent | `intent_router.decide_intent` | `routing.{scores,rules_evaluated,decision_path,winning_rule}` |
| 4. Hours follow-up promote | if route was `rag` but “tomorrow?” after hours | may rewrite to `opening_hours` |
| 5. Branch | tool / RAG / escalation / unknown | `route`, `pipeline_steps[]`, domain payloads |
| 6. Optional adapt | `adapt_structured_fact` (main LLM) | `adapt.{ran,why,model}` |
| 7. Persist | append user+bot turns | — |
| 8. Return | `ChatResponse` | `answer`, `citations`, `slots?`, `trace`, `latency_ms` |

### 2.1 Branch semantics

```text
route ∈ structured fact set
  → load JSON/MD fact → optional adapt → pipeline rag=skipped

route = opening_hours
  → parse datetime → holiday/Sunday/HKO → adapt → rag skipped

route ∈ {booking_tool, booking_dialogue, cancel_dialogue}
  → CSV register / sticky dialogue state machine

route = rag
  → retrieve(top_k) → lexical rerank → generate → citations[rank,score,text]

route = staff_escalation
  → parent-safe ack + staff_messages row (+ email body)

route = unknown_programme | soft KB gap
  → parent refuse + admin_todos row (no hallucinated programme)

route = off_domain
  → refuse · no admin todo
```

---

## 3. Hybrid intent router

**File:** `backend/services/intent_router.py`  
**Supporting:** `centre_facts.py`, `escalation.py`, `intent_classifier.py` (prototypes), `intent_arbiter.py` (cheap LLM).

### 3.1 Scoring pipeline

```text
message
  │
  ├─ hard detectors → scores[route] ∈ [0.85, 1.0]
  │     escalation, off_domain, severe_weather, illness, allergy, fees, …
  │     booking_tool / booking_dialogue / cancel / trial_info / hours
  │
  ├─ conflict resolution (destructive)
  │     weather>hours, payment>fees, illness>fees, availability>fees, …
  │     waiting clears pickup + booking_tool
  │     multi_topic>single_fact → drop fact keys, scores[rag]=0.65
  │
  ├─ soft prototype blend  (skipped if any hard veto score ≥ 0.98)
  │     intent_classifier.score_prototypes → blend_scores
  │
  ├─ clean floor (drop scores < 0.28 except availability alias)
  │
  ├─ _pick_route via _PRIORITY tie-break
  │
  └─ cheap LLM arbiter (optional)
        skip if top≥0.9 ∧ ¬material_conflicts ∧ route ∉ {confused,off_domain,rag}
        else review_intent → keep | reroute | force rag
```

### 3.2 Priority order (high → low)

```text
staff_escalation > off_domain > severe_weather > illness > allergy >
makeup > teachers > pickup > waiting > sibling > guest_policy >
location > payment > fees > pets_policy > unsupported_facility >
unknown_programme > cancel_dialogue > trial_info > booking_dialogue >
booking_tool > opening_hours > greeting > thanks > confused > rag
```

Hard veto set (`_HARD_VETO`, blocks prototype dilution):  
`staff_escalation`, `off_domain`, `severe_weather`, `confused`, `thanks`.

### 3.3 Arbiter gate

```text
IF max(scores) ≥ 0.9
   AND no material conflicts (ignoring blended_prototypes)
   AND route ∉ {confused, off_domain, rag}
THEN
   arbiter.called = false
   reason = high_confidence_skip
ELSE
   cheap model review_intent(message, proposed_route, scores, history[-4:])
```

`trace.routing.winning_rule` records `pre_arbiter_route`, `arbiter_called`, `arbiter_reason`, `skip_detail`, `override`.

### 3.4 Rule → downstream map

| Rule id | Detector | Typical score | Downstream |
|---------|----------|---------------|------------|
| `staff_escalation` | ordered: safety → angry → hard_conflict → ask_human | 1.0 | inbox + email draft |
| `off_domain` | politics / foreign-city weather / crypto / malware | 0.98 | refuse, no todo |
| `severe_weather` | `_WEATHER_POLICY_RE` (T8, black rain, 風球…) | 1.0 | `facts/severe_weather.json` + adapt |
| `fees` | `_FEE_RE` minus `_FEE_DURATION_RE` | 0.9 | `facts/fees.json` + adapt |
| `booking_tool` | `_AVAIL_STRONG_RE` / seat browse | 0.85–0.95 | CSV `seats_left` cards |
| `booking_dialogue` | book verbs + sticky state | ~0.85 | multi-turn book FSM |
| `cancel_dialogue` | cancel verbs + sticky | 0.95 | multi-turn cancel FSM |
| `opening_hours` | `detect_opening_hours_intent` | 0.8 | calendar engine |
| `unknown_programme` | catalogue miss | 0.95 | refuse + sheet row |
| `rag` | default / multi-topic | 0.5–0.65 | Chroma → Qwen |

### 3.5 Inspector evidence (`rules_evaluated[]`)

Each rule emits:

| Field | Meaning |
|-------|---------|
| `matched` | predicate true after scoring |
| `score` | numeric score if present |
| `mechanism` | which code path owns the rule |
| `pattern_summary` | human summary of the detector |
| `matched_span` | `{text,start,end}` if a regex hit |
| `why` / `why_not` | plain-language trigger or idle reason |
| `checks[]` | nested `{label, passed, detail}` sub-conditions |
| `downstream` | what runs if this rule wins |
| `suppressed_by_conflict` | matched then cleared by conflict resolution |

### 3.6 Routing invariants (stress-tested)

- Typhoon / T8 / black rain → `severe_weather` (RAG skipped; adapt may run).
- “How much is phonics?” → `fees` (not RAG). “Do you offer private 1:1?” → `rag`.
- “How much time does phonics take?” → not fees (`_FEE_DURATION_RE` exclusion) → RAG.
- Strong seat browse (`seats left`, `有冇位`, `仲有冇位`) → `booking_tool`, even if fee words co-occur (`availability>fees`).
- Bare waitlist (`候補`, `滿班`, `waiting list`) → `waiting`; bare `冇位` no longer implies waitlist (would steal Cantonese seat asks).
- Safety “allergy + reaction” → `staff_escalation` before non-urgent `allergy` FAQ.
- Off-domain city weather → refuse without admin todo.

Probe script: `scripts/stress_intent_router.py`.

---

## 4. Structured tools

### 4.1 Centre facts

`backend/services/centre_facts.py` + `backend/facts/*.json` (SAE image path; not the mutable `/app/data` volume).

Flow: intent → `format_*_answer` → citations pointing at JSON/MD → `adapt_structured_fact(question, answer, route=…)` rewrites tone only (`enable_thinking: false`).

### 4.2 Opening hours

`detect_opening_hours_intent` → `_get_opening_facts` / `compute_opening_answer`:

1. Resolve datetime (explicit date, weekday name, or now).
2. Public holiday table → closed + name.
3. Sunday → closed.
4. Else weekday open/close window.
5. Optional HKO severe-weather hint when query is “today”.

### 4.3 Seats & booking

CSV under `backend/data/sheets/`: teachers, windows, sessions, enrolments, trial_slots, families.

- `booking_tool`: compute `seats_left = capacity − enrolments`, return bookable `slots[]`.
- `booking_dialogue` / `cancel_dialogue`: session-sticky FSM; identity from `phone` → `families.csv`.
- Confirmed chat books append `chat_bookings.csv` (provisional, no payment).

### 4.4 Staff surfaces

| Surface | Store | Trigger |
|---------|-------|---------|
| Follow-up sheet | `admin_todos` | unknown programme / soft RAG gap |
| Inbox | `staff_messages` | escalation categories |
| Email draft | `staff_email` | optional outbound; body kept for inbox UI |

Parent chat copy never names internal sheets.

---

## 5. RAG pipeline

Training-free by design: KB edits → reingest; no fine-tune loop.

### 5.1 Ingest

`rag/ingest.py` + `rag/chunking.py`:

- Walk `content/{lang}/**/*.md` (frontmatter → metadata).
- Chunk size `RAG_CHUNK_SIZE` (default 900) with overlap `RAG_CHUNK_OVERLAP` (120).
- Embed (`EMBEDDING_MODEL`, default `text-embedding-v4`) → upsert Chroma collection `brightnest_kb`.
- Offline fallback: deterministic local hash embeddings.

### 5.2 Retrieve → generate

`rag/pipeline.py::answer_question`:

```text
embed(query)
  → Chroma query (pool_k = top_k, or ≥20 when age digits present)
  → optional language-filter retry
  → age FAQ overlay merge
  → lexical rerank (path/alias boost, folder prior, token overlap, vector score)
  → truncate to rag_top_k (default 5)
  → generate_answer_meta(contexts, history_context)
  → citations[{rank, score, snippet, text, source, title, char_count}]
  → gap heuristics (weak_score, off_topic, NO_ANSWER, soft hand-off)
```

Weak-score thresholds: cloud ≈ 0.28, local-hash ≈ 0.42.

Generation: DashScope chat (`LLM_MODEL`) with numbered excerpts; `enable_thinking: false`; extractive fallback if no key.

### 5.3 Debug payload (`trace.rag`)

`index_size`, `top_k`, `hit_count`, `top_score`, `embedding_mode`, `language_fallback`, `gap_reason`, `hits[]` (full `text`).

---

## 6. Session memory

| Setting | Default |
|---------|---------|
| `CHAT_HISTORY_ENABLED` | true |
| `CHAT_HISTORY_LIMIT` | 10 messages |
| Store | SQLite `CHAT_HISTORY_DB` |

Consumed by: RAG prompt, fact adapt, hours follow-up (“tomorrow?”), arbiter history preview (last 4 turns).  
`trace.history.turns[]` exposes full loaded window to the inspector.

---

## 7. Models & flags

| Role | Env | Default / notes |
|------|-----|-----------------|
| Main LLM | `LLM_MODEL` | e.g. `qwen3.8-flash` — RAG + adapt |
| Cheap arbiter | `INTENT_ARBITER_MODEL` | falls back to `LLM_MODEL` |
| Embeddings | `EMBEDDING_MODEL` | `text-embedding-v4` |
| Arbiter master | `INTENT_ARBITER_ENABLED` | true |
| Adapt extras | `ARBITER_ADAPT_EXTRA` | true |
| Gap polish | `ARBITER_GAP_REWRITE` | true |
| Gen | `GEN_MAX_TOKENS` / `GEN_TEMPERATURE` | 280 / 0.3 |

---

## 8. Demo UI contract

Left: parent chat + probes + seat cards.  
Right:

- **System** — route pill, `pipeline_steps`, Router decision (path + expandable rule evidence), hours panel, RAG hits (rank/score/full text), register, booking, session memory.
- **Staff desk** — Sheets-style follow-ups | Gmail-style inbox (`**bold**` rendered).

Canonical probes:

| Probe | Route |
|-------|-------|
| Typhoon | `severe_weather` |
| Phonics fee | `fees` |
| RAG · Private 1:1 | `rag` |
| Phonics seats | `booking_tool` |
| Unknown ask | `unknown_programme` |
| Safety call | `staff_escalation` |

---

## 9. Local run

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

```powershell
cd ..\developer-site\frontend\tailwindcss4
npm install
npm run dev
```

Tests: `pytest tests/test_intent_routing.py -q`  
Stress: `python scripts/stress_intent_router.py`

---

## 10. Related

- [architecture.md](architecture.md) — compact diagram  
- [demo-script.md](demo-script.md) — 5-minute talk track  
- [DEVELOPER_SITE_INTEGRATION.md](DEVELOPER_SITE_INTEGRATION.md)  
- [../deploy/REDEPLOY.md](../deploy/REDEPLOY.md)  
