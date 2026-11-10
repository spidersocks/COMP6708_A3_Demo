# BrightNest — system architecture (COMP6708 · Group 15)

| Member | Name | Student ID |
|--------|------|------------|
| 1 | FONTAINE Sean Hsien-ming | 26071164r |
| 2 | JIANG Junwei | 26107939r |
| 3 | MAO Shiwei | 25038479r |
| 4 | YAN Xingzhuo | 25013043r |

**Live demo:** https://seanfontaine.dev/COMP6708_A3_Demo  
**API:** Alibaba SAE, reached from the SPA via same-origin `/brightnest-api`  
**Code:** FastAPI (`backend/`) · React inspector (`developer-site` BrightNestBot) · KB `content/`

Fictional brand **BrightNest / 亮巢**. This document explains **what the system does**, **why each path exists**, and **how a single parent message becomes an answer** — written so a teammate or marker can follow it without reading every source file first.

---

## 0. One-minute picture

BrightNest is a WhatsApp-style parent assistant for a learning centre.

1. The parent types a message.
2. A **hybrid intent router** decides *which specialist* should answer (fee table, typhoon policy, live seats, booking dialogue, staff escalation, or the knowledge-base RAG path).
3. That specialist produces a grounded answer (JSON fact, CSV register, or retrieved Markdown chunks).
4. Optionally a main LLM **rewrites tone only** (`adapt`) — it must not invent new policies.
5. The demo UI shows the decision: route, exact cue in the query, every rule’s pass/fail checks, RAG chunks, and staff surfaces.

```text
Parent message
    │
    ▼
Intent router  ──►  structured tool   OR   RAG   OR   staff / refuse
    │                    │                   │
    │                    ▼                   ▼
    │              facts / CSV / FSM    Chroma → Qwen
    │                    │                   │
    └──────────► optional tone adapt ◄───────┘
                         │
                         ▼
                   answer + trace
```

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
       ├─ services/intent_router.py     hybrid scores + arbiter gate + match evidence
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

| Step | Module | What you see on `trace` |
|------|--------|-------------------------|
| 1. Language | `get_language_code` | `language` |
| 2. History | `get_recent_history` | `history.turns[]` (full window for the inspector) |
| 3. Intent | `intent_router.decide_intent` | `routing.{scores, rules_evaluated, decision_path, winning_rule, match_summary}` |
| 4. Hours follow-up | promote “tomorrow?” after an hours turn | may rewrite `rag` → `opening_hours` |
| 5. Branch | tool / RAG / escalation / unknown | `route`, `pipeline_steps[]`, domain payloads |
| 6. Optional adapt | `adapt_structured_fact` | `adapt.{ran, why, model}` |
| 7. Persist | append user + bot turns | — |
| 8. Return | `ChatResponse` | `answer`, `citations`, `slots?`, `trace` |

### 2.1 Three answer tiers (plain language)

BrightNest does **not** treat every structured hit the same. After the router picks a route:

| Tier | Routes (examples) | What runs | RAG? |
|------|-------------------|-----------|------|
| **1 · Hard tool** | `severe_weather`, `opening_hours`, `booking_*`, `cancel_*`, `staff_escalation`, `off_domain`, `unknown_programme` | Structured answer (JSON / CSV / FSM). Optional **tone-only** adapt. | **Bypass** — retrieve skipped |
| **2 · Grounded compose** | `allergy`, `illness`, `fees`, `teachers`, `makeup`, `sibling`, `guest_policy`, `waiting`, `pickup`, `payment`, `location`, `trial_info`, `pets_policy` | Load fact card → retrieve top‑k KB → `compose_grounded_fact` (card is privileged; KB may elaborate) | **Enrich only** |
| **3 · Pure RAG** | `rag` (default / multi-topic) | Retrieve → generate with citations | **Full** generate |

Flag: `GROUNDING_ENRICH_ENABLED` (default true). Enrich top‑k: `GROUNDING_ENRICH_TOP_K` (default 3).

Inspector: `trace.grounding.{mode,why,composed,rag_hit_count}` and `trace.rag.mode=grounded_enrich` when enrich runs.

---

## 3. Hybrid intent router (the decision brain)

**Files:** `backend/services/intent_router.py`  
**Helpers:** `centre_facts.py` (regex facts), `escalation.py`, `intent_classifier.py` (soft prototypes), `intent_arbiter.py` (cheap LLM review).

### 3.1 Why hybrid?

| Layer | Role | Example |
|-------|------|---------|
| Hard regex / detectors | High-stakes intents must be deterministic | Typhoon → always policy fact, never a vague RAG paragraph |
| Conflict resolution | When two detectors fire, pick the safer owner | Weather beats hours; strong seats beat fees |
| Soft prototypes | Catch paraphrases the regex missed | Blended only when no hard veto (≥ 0.98) |
| Cheap arbiter | Second look on ambiguous turns | Skipped when top score ≥ 0.9 and no material conflict |

### 3.2 Where intent scores come from (critical)

**Intent scores are not RAG similarity.** They are mostly **fixed awards** from named detectors.

| Source id | What it means | Example |
|-----------|---------------|---------|
| `hard_regex` | Detector / regex / sticky state matched → assign a **fixed** score | `allergy=0.95`, `fees=0.90`, `severe_weather=1.0` |
| `sticky_state` | Session already in book/cancel dialogue | `booking_dialogue=0.85` |
| `prototype_blend` | Soft prototype similarities blended in (unless hard veto ≥ 0.98) | Score may move slightly; hits ≥ 0.95 are protected |
| `conflict_clear` | Higher-priority conflict removed this route | `availability>fees` clears `fees` |
| `default_floor` | Nothing else owns the turn | `rag=0.5` or multi-topic `rag=0.65` |

Every award / blend / clear is appended to `trace.routing.score_ledger[]`.  
Per-route snapshot: `trace.routing.score_origins`.  
Legend for the UI: `trace.routing.score_source_guide`.

### 3.3 Scoring pipeline

```text
message
  │
  ├─ hard detectors → scores[route] ∈ [0.85, 1.0]   ← fixed awards (ledger: award)
  │     escalation, off_domain, severe_weather, illness, allergy, fees, …
  │     booking_tool / booking_dialogue / cancel / trial_info / hours
  │
  ├─ conflict resolution (destructive)              ← ledger: clear
  │     weather>hours, payment>fees, illness>fees, availability>fees, …
  │     waiting clears pickup + booking_tool
  │     multi_topic>single_fact → drop fact keys, scores[rag]=0.65
  │
  ├─ soft prototype blend  (skipped if any hard veto score ≥ 0.98)
  │                                                     ← ledger: blend
  ├─ clean floor (drop scores < 0.28 except availability alias)
  │
  ├─ _pick_route via _PRIORITY tie-break
  │
  └─ cheap LLM arbiter (optional)
        skip if top≥0.9 ∧ ¬material_conflicts ∧ route ∉ {confused,off_domain,rag}
        else review_intent → keep | reroute | force rag
```

### 3.4 Priority order (high → low)

```text
staff_escalation > off_domain > severe_weather > illness > allergy >
makeup > teachers > pickup > waiting > sibling > guest_policy >
location > payment > fees > pets_policy > unsupported_facility >
unknown_programme > cancel_dialogue > trial_info > booking_dialogue >
booking_tool > opening_hours > greeting > thanks > confused > rag
```

Hard veto set (`_HARD_VETO`, blocks prototype dilution):  
`staff_escalation`, `off_domain`, `severe_weather`, `confused`, `thanks`.

### 3.5 Arbiter gate

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

### 3.6 Worked examples (read these first)

These are the stories the demo is built to tell. Each row is what you should see in **Router decision → What matched in this query**.

#### A. Typhoon — structured policy, not RAG

| | |
|--|--|
| **Parent says** | “What happens when there is a typhoon signal 8?” |
| **Exact cue** | `typhoon` / `signal 8` (weather-policy regex) |
| **Route** | `severe_weather` |
| **Why not RAG** | High-stakes ops answers come from `facts/severe_weather.json`, then optional tone adapt |
| **UI** | Match card highlights the weather cue; RAG panel shows *Skipped · route=severe_weather* |

#### B. Monthly fee in Cantonese — fee table

| | |
|--|--|
| **Parent says** | `月費幾多？` |
| **Exact cue** | `月費` (fee regex also covers `學費`, `費用`, `班費`, `幾錢`, …) |
| **Route** | `fees` |
| **Why this wording mattered** | Earlier builds only caught `學費` / `幾多錢`, so bare `月費幾多？` fell through to RAG. That gap is closed. |
| **Not fees** | `小朋友幾多歲…` — bare `幾多` alone is deliberately **not** a fee cue |

#### C. Seats vs waitlist (same language family, different route)

| Parent says | Cue | Route |
|-------------|-----|-------|
| `仲有冇位？` | `有冇位` / `冇位` as **seat browse** | `booking_tool` → live CSV seats |
| `冇位，可以候補嗎` | waitlist language (`候補` / full-class) | `waiting` → waitlist policy fact |
| `有冇位同月費幾多？` | strong availability **wins over** fees | `booking_tool` (`availability>fees`) |

#### D. Private 1:1 — real RAG

| | |
|--|--|
| **Parent says** | “Do you offer private 1:1 tutoring?” / “What is BrightNest Private 1:1 coaching?” |
| **Route** | `rag` |
| **Why** | No structured fact owns this; KB Markdown is retrieved (rank, score, full chunk text in the inspector) |

#### E. Safety escalation

| | |
|--|--|
| **Parent says** | allergy + reaction + “call me ASAP” |
| **Route** | `staff_escalation` (before the non-urgent `allergy` FAQ) |
| **Side effect** | Staff inbox row (+ email draft body) |

### 3.7 Rule → downstream map

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

### 3.8 Inspector evidence (what “open a rule” shows)

The router does not only say *which* rule won. For every rule it emits an evidence object the UI can expand.

| Field | Meaning |
|-------|---------|
| `matched` | Predicate true after scoring |
| `score` | Numeric score if present |
| `query` | Full parent message for this turn |
| `matched_span` | `{text, start, end}` when a regex hit a substring |
| `match_evidence` | Rich payload: `cue`, character offsets, `segments[]` (for highlight), `annotated`, `explanation`, `kind` |
| `why` / `why_not` | Plain-language fire / idle reason |
| `checks[]` | Nested `{label, passed, detail}` sub-conditions |
| `mechanism` / `pattern_summary` | Which code path / what the detector looks for |
| `downstream` | What runs if this rule wins |
| `score_origin` | `{source, why, score}` — **why this number exists** (not RAG sim) |
| `suppressed_by_conflict` | Matched then cleared by conflict resolution |

**Winning glance card** — `trace.routing.match_summary`:

- `headline` — one sentence (“Matched because the message contains «月費» → route `fees`.”)
- `cue` — exact substring
- `segments` — full query split into highlighted / non-highlighted pieces
- `explanation` / `downstream` — why that cue matters and what runs next

In the UI: **Router decision** opens with that card; the winning rule auto-expands; click any other rule to see the same query with *that* rule’s cue (or why it stayed idle).

### 3.9 Routing invariants (stress-tested)

- Typhoon / T8 / black rain → `severe_weather` (RAG skipped; adapt may run).
- “How much is phonics?” / `月費幾多？` / `幾錢一個月` → `fees` (not RAG).
- “Do you offer private 1:1?” → `rag`.
- “How much time does phonics take?” → not fees (`_FEE_DURATION_RE`) → RAG / other.
- Strong seat browse (`seats left`, `有冇位`, `仲有冇位`) → `booking_tool`, even if fee words co-occur.
- Bare waitlist (`候補`, `滿班`, `waiting list`) → `waiting`; bare `冇位` alone no longer implies waitlist.
- Safety “allergy + reaction” → `staff_escalation` before non-urgent `allergy` FAQ.
- Off-domain city weather → refuse without admin todo.

Probe script: `scripts/stress_intent_router.py` (50+ cases).

---

## 4. Structured tools

### 4.1 Centre facts

`backend/services/centre_facts.py` + `backend/facts/*.json` (baked into the SAE image; not the mutable `/app/data` volume).

Flow: intent → `format_*_answer` → citations pointing at JSON/MD → `adapt_structured_fact(question, answer, route=…)` rewrites **tone only** (`enable_thinking: false`).

### 4.2 Opening hours

`detect_opening_hours_intent` → calendar engine:

1. Resolve datetime (explicit date, weekday name, or now).
2. Public holiday table → closed + name.
3. Sunday → closed.
4. Else weekday open/close window.
5. Optional HKO severe-weather hint when the query is “today”.

### 4.3 Seats & booking

CSV under `backend/data/sheets/`: teachers, windows, sessions, enrolments, trial_slots, families.

- `booking_tool`: `seats_left = capacity − enrolments`, return bookable `slots[]`.
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

Training-free by design: edit Markdown → reingest; no fine-tune loop.

### 5.1 Ingest

`rag/ingest.py` + `rag/chunking.py`:

- Walk `content/{lang}/**/*.md` (frontmatter → metadata).
- Chunk size `RAG_CHUNK_SIZE` (default 900) with overlap `RAG_CHUNK_OVERLAP` (120).
- Embed (`EMBEDDING_MODEL`, default `text-embedding-v4`) → upsert Chroma collection `brightnest_kb`.
- Offline fallback: deterministic local hash embeddings.

### 5.2 Retrieve → generate

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
Generation: DashScope chat with numbered excerpts; `enable_thinking: false`.

### 5.3 Debug payload (`trace.rag`)

`index_size`, `top_k`, `hit_count`, `top_score`, `embedding_mode`, `language_fallback`, `gap_reason`, `hits[]` (full `text`).

The inspector only treats `rag.hits` as retrieval evidence when `route === "rag"` — tool citations must not masquerade as KB chunks.

---

## 6. Session memory

| Setting | Default |
|---------|---------|
| `CHAT_HISTORY_ENABLED` | true |
| `CHAT_HISTORY_LIMIT` | 10 messages |
| Store | SQLite `CHAT_HISTORY_DB` |

Used by: RAG prompt, fact adapt, hours follow-up (“tomorrow?”), arbiter history preview (last 4 turns).  
`trace.history.turns[]` exposes the full loaded window to the inspector.

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

**Left:** parent chat + probe chips + seat cards.  
**Right — System tab:**

1. Route pill + pipeline step rail
2. **Router decision** — match summary (highlighted cue) → path → expandable rule map
3. Hours / holiday / weather panel
4. RAG hits (rank, score, full chunk) — only when route is RAG
5. Register, booking, session memory

**Right — Staff desk:** Sheets-style follow-ups (formula bar shows full cell text) | Gmail-style inbox (`**bold`** rendered).

Canonical probes:

| Probe | Expected route | What to point at |
|-------|----------------|------------------|
| Typhoon | `severe_weather` | Match cue + RAG skipped |
| Phonics fee / `月費幾多？` | `fees` | Highlighted fee cue |
| RAG · Private 1:1 | `rag` | Chunk rank/score/text |
| Phonics seats / `仲有冇位？` | `booking_tool` | Seat cards from CSV |
| Unknown ask | `unknown_programme` | Follow-up sheet row |
| Safety call | `staff_escalation` | Inbox + email draft |

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

## 10. Glossary

| Term | Meaning |
|------|---------|
| **Route** | Named specialist chosen for this turn (`fees`, `rag`, …) |
| **Cue / matched span** | Exact substring in the parent message that fired a detector |
| **Adapt** | Main-LLM tone rewrite of a structured fact; must not invent policy |
| **Arbiter** | Cheap LLM that may confirm or override an ambiguous route |
| **Hard veto** | Ultra-high-confidence score that blocks soft prototype dilution |
| **RAG** | Retrieve Markdown chunks from Chroma, then generate with citations |
| **Sticky dialogue** | Book/cancel FSM that stays active across turns for one `session_id` |

---

## 11. Related

- [architecture.md](architecture.md) — compact diagram  
- [demo-script.md](demo-script.md) — 5-minute talk track  
- [DEVELOPER_SITE_INTEGRATION.md](DEVELOPER_SITE_INTEGRATION.md)  
- [../deploy/REDEPLOY.md](../deploy/REDEPLOY.md)
