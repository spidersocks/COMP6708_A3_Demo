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

Fictional brand **BrightNest / 亮巢**. This document explains **what the system does**, **why each path exists**, and **how a single parent message becomes an answer**.

---

## 0. One-minute picture

BrightNest is a WhatsApp-shaped parent assistant for a fictional HK learning centre.

1. The parent types a message (demo phone UI — chat only, no booking form).
2. A **hybrid intent router** scores detectors and picks a specialist (fee table, typhoon policy, live seats, booking/cancel FSM, staff escalation, refuse, or pure RAG).
3. That specialist answers from structured truth (JSON / CSV / calendar) and/or retrieved Markdown.
4. Soft FAQ routes **grounded-compose** (privileged card/live doc + KB enrich). Hard FSM paths **bypass retrieve**, then a **cheap LLM final check** may `proceed` / `clarify` / `abort` before irreversible writes.
5. Soft answers may get a main-LLM **tone rewrite** (`adapt`) — it must not invent policy.
6. The inspector shows the decision: route, cue, score ledger, final check, grounding, RAG chunks, SheetPro / MailBox.

```text
Parent message
    │
    ▼
Intent router (+ optional route arbiter)
    │
    ├─ soft FAQ ──► grounded compose (card/live doc privileged + KB) ──► optional adapt
    ├─ hard FSM ──► book / cancel / escalate / refuse ──► final check ──► write or clarify
    └─ pure RAG ──► Chroma → Qwen + citations (+ gap polish / SheetPro if needed)
    │
    ▼
answer + trace (inspector)
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
       ├─ services/intent_router.py     hybrid scores + route arbiter + match evidence
       ├─ services/intent_arbiter.py    cheap LLM keep|rag on ambiguous routes
       ├─ services/final_check.py       cheap LLM proceed|clarify|abort before commits
       ├─ services/centre_facts.py      structured JSON facts + regex intents
       ├─ llm/opening_hours.py          deterministic calendar / holidays / HKO
       ├─ services/booking_*.py         sticky book dialogue + CSV register
       ├─ services/cancel_dialogue.py   sticky cancel FSM + cancellations sheet
       ├─ services/escalation.py        safety / anger / ask-human
       ├─ services/staff_*.py           MailBox inbox + optional mail draft
       ├─ services/admin_todos.py       SheetPro follow-up rows
       ├─ services/demo_polish.py       gap rewrite · booking parse · cancel pick
       ├─ rag/{ingest,pipeline,generator,store,embeddings}.py
       │     content/{en,zh-HK,zh-CN}/**/*.md  →  Chroma  →  Qwen
       └─ llm/chat_history.py           SQLite session memory
```

| Tier | Host | Artifact |
|------|------|----------|
| UI | Vercel (`developer-site`) | Vite React |
| API | SAE `cn-hongkong` | `deploy/Dockerfile.backend` → ACR → DeployApplication |
| Models | DashScope (HK / CN endpoint via `DASHSCOPE_BASE_URL`) | chat + embeddings |

---

## 2. Request lifecycle (`POST /chat`)

Entry: `backend/router.py::chat`.

| Step | Module | What you see on `trace` |
|------|--------|-------------------------|
| 1. Language | `get_language_code` | `language` |
| 2. History | `get_recent_history` | `history.turns[]` + full `history.preview` (exact LLM context string) |
| 3. Intent | `intent_router.decide_intent` | `routing.{scores, rules_evaluated, decision_path, winning_rule, match_summary, score_ledger}` |
| 4. Hours follow-up | promote “tomorrow?” after an hours turn | may rewrite `rag` → `opening_hours` |
| 5. Branch | tool / FSM / grounded compose / RAG / escalation | `route`, `pipeline_steps[]`, domain payloads |
| 6. Final check | `final_check.review_commit` on hard commits | step `final_check` · `debug.final_check` |
| 7. Optional adapt / compose | `compose_grounded_fact` or `adapt_structured_fact` | `grounding.*`, `adapt.*`, `rag` enrich hits |
| 8. Persist | append user + bot turns | — |
| 9. Return | `ChatResponse` | `answer`, `citations`, `slots?` (API only), `trace` |

### 2.1 Three answer tiers

Tools own live / multi-turn work. Static FAQs (fees, address, payment, teachers, pickup, …) are **pure RAG**.

| Tier | Routes (examples) | What runs | Retrieve? |
|------|-------------------|-----------|-----------|
| **1 · Hard FSM / hand-off** | `booking_dialogue`, `cancel_dialogue`, `staff_escalation`, `off_domain` | Sticky dialogue / inbox / refuse templates. **Final check** before book assessment write, cancel write, escalate inbox, or hard refuse. | **Bypass** |
| **2 · Live tools (+ enrich)** | `severe_weather`, `opening_hours`, `booking_tool` | Live HKO / holiday calendar / seat register → optional KB enrich via `compose_grounded_fact`. | **Enrich only** |
| **3 · Pure RAG** | `rag` (default for all static FAQs + programmes) | Retrieve → generate with citations; off-topic gate may refuse | **Full** generate |

**Weather:** live HKO (when available) is a privileged live doc; centre severe-weather JSON is the structured card; KB may enrich.  
**Seats / booking (WhatsApp-shaped):** seat counts are **chat text** (+ Class register in the inspector). Named programmes use `booking_dialogue` → `confirm_assessment` → parent **yes** → **final check** → SheetPro `assessment_request`. No booking form / Book buttons.

Flags: `GROUNDING_ENRICH_ENABLED` (default true), `GROUNDING_ENRICH_TOP_K` (default 3).  
Inspector: `trace.grounding.{mode,why,composed,live_doc,ask_tool_ok,invoke_tool,rag_hit_count}` and `trace.rag.mode=grounded_enrich` when enrich runs.

---

## 3. Hybrid intent router (the decision brain)

**Files:** `backend/services/intent_router.py`  
**Helpers:** `centre_facts.py` (regex facts), `escalation.py`, `intent_classifier.py` (soft prototypes), `intent_arbiter.py` (route review), `final_check.py` (commit gate).

### 3.1 Why hybrid?

| Layer | Role | Example |
|-------|------|---------|
| Hard regex / detectors | Tool intents must be deterministic | Typhoon → weather tool; seats → register; book/cancel → FSM |
| Conflict resolution | When two tool detectors fire, pick the safer owner | Weather beats hours |
| Soft prototypes | Catch paraphrases the regex missed | Blended only when no hard veto (≥ 0.98) |
| Cheap route arbiter | Second look on ambiguous **tool** routes | Skipped when top score ≥ 0.9 and no material conflict |
| Cheap **final check** | Second look before **commits** (book / cancel / escalate / refuse) | `proceed` · `clarify` · `abort` (fail-open; safety never blocked) |

### 3.2 Where intent scores come from

**Intent scores are not RAG similarity.** They are **fixed awards** from tool detectors only.

| Source id | What it means | Example |
|-----------|---------------|---------|
| `hard_regex` | Tool detector / sticky state matched → fixed score | `severe_weather=1.0`, `booking_tool=0.95` |
| `sticky_state` | Session already in book/cancel dialogue | `booking_dialogue=0.85` |
| `prototype_blend` | Soft prototype similarities blended in (unless hard veto ≥ 0.98) | Score may move slightly; hits ≥ 0.95 are protected |
| `conflict_clear` | Higher-priority conflict removed this route | `weather>hours` clears `opening_hours` |
| `default_floor` | No tool owns the turn | `rag=0.5` |

Every award / blend / clear is appended to `trace.routing.score_ledger[]`.  
Per-route snapshot: `trace.routing.score_origins`.  
Legend for the UI: `trace.routing.score_source_guide`.

### 3.3 Scoring pipeline

```text
message
  │
  ├─ tool detectors → scores[route] ∈ [0.8, 1.0]   ← fixed awards (ledger: award)
  │     escalation, off_domain, severe_weather,
  │     cancel / booking_dialogue / booking_tool / opening_hours
  │
  ├─ conflict resolution                              ← ledger: clear
  │     weather>hours
  │
  ├─ soft prototype blend  (skipped if any hard veto score ≥ 0.98)
  │                                                     ← ledger: blend
  ├─ clean floor (drop scores < 0.28 except availability alias)
  │
  ├─ _pick_route via _PRIORITY tie-break
  │
  └─ cheap LLM route arbiter (optional)
        skip if top≥0.9 ∧ ¬material_conflicts ∧ route ∉ {off_domain,rag}
        else review_intent → keep | rag
        sticky book/cancel mid-flight: arbiter skipped
        staff_escalation: never second-guessed by route arbiter

Later, on hard commits only:
  final_check.review_commit(kind) → proceed | clarify | abort
```

### 3.4 Priority order (high → low)

```text
staff_escalation > off_domain > severe_weather > cancel_dialogue >
booking_dialogue > booking_tool > opening_hours > rag
```

Hard veto set (`_HARD_VETO`, blocks prototype dilution):  
`staff_escalation`, `off_domain`, `severe_weather`.

### 3.5 Route arbiter gate

```text
IF max(scores) ≥ 0.9
   AND no material conflicts (ignoring blended_prototypes)
   AND route ∉ {off_domain, rag}
THEN
   arbiter.called = false
   reason = high_confidence_skip
ELSE
   cheap model review_intent(message, proposed_route, scores, history[-4:])
```

### 3.5b Commit final check (`services/final_check.py`)

Hard-path **RAG bypass** means retrieve is skipped on the answer path — not that the LLM is absent. Before irreversible or staff-visible writes:

| Kind | When | On `clarify` | On `abort` |
|------|------|--------------|------------|
| `book` | Parent affirms `confirm_assessment` | Re-ask; no SheetPro row | Clear dialogue; stay helpful |
| `cancel` | Parent affirms cancel confirm | Re-ask; no `cancellations.csv` row | Keep class booked |
| `escalate` | Before MailBox write | Ask staff vs FAQ; no inbox yet | Soft centre help (no inbox) |
| `refuse` | Before hard `off_domain` refuse | Soft re-ask | Fall through to **pure RAG** |

**Also true:**
- Fail-open: LLM empty/error → `proceed`.
- Safety escalations set `force_proceed` so clarify/abort cannot delay the inbox.
- Cancel declines (`no` / `do not cancel`) abort in the FSM **before** final check.
- `unknown_programme` / soft RAG gaps write SheetPro **without** final check.
- History “yes” after an assessment offer can queue SheetPro without going through `final_check` (legacy affirm path) — prefer the live Book Jason probe which uses the FSM + gate.

Inspector: pipeline step `final_check` (`action` · `reason` · model) and/or `debug.final_check`. Flag: `ARBITER_FINAL_CHECK` (default true).

### 3.6 Worked examples (read these first)

These are the stories the demo is built to tell. Each row is what you should see in **Router decision → What matched in this query**.

#### A. Typhoon — grounded compose (not pure RAG)

| | |
|--|--|
| **Parent says** | “What happens when there is a typhoon signal 8?” |
| **Exact cue** | `typhoon` / `signal 8` (weather-policy regex) |
| **Route** | `severe_weather` |
| **Why not pure RAG** | Ops answer is owned by `facts/severe_weather.json` (+ live HKO doc when available). KB may **enrich**; the card/live doc stay privileged. |
| **UI** | Match card highlights the weather cue; **Answer grounding** shows grounded compose; enrich hits may appear under RAG enrichment |

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
| `仲有冇位？` | `有冇位` / `冇位` as **seat browse** | `booking_tool` → live CSV seats **in chat text** |
| `冇位，可以候補嗎` | waitlist language (`候補` / full-class) | `waiting` → waitlist policy fact |
| `有冇位同月費幾多？` | strong availability **wins over** fees | `booking_tool` (`availability>fees`) |
| “Book Jason” → named programme → “Yes” | sticky FSM + affirm | `booking_dialogue` → **final check** → SheetPro `assessment_request` |

#### D. Private 1:1 — pure RAG

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
| **Side effect** | **Final check** (`force_proceed` on safety) → MailBox row + email draft |

#### F. Cancel decline

| | |
|--|--|
| **Parent says** | cancel → pick class → `no` / `do not cancel` |
| **Route** | sticky `cancel_dialogue` |
| **Result** | “Kept as booked — nothing was cancelled.” (FSM abort; no sheet write) |

### 3.7 Rule → downstream map

| Rule id | Detector | Typical score | Downstream |
|---------|----------|---------------|------------|
| `staff_escalation` | ordered: safety → angry → hard_conflict → ask_human | 1.0 | final check → MailBox + email draft |
| `off_domain` | politics / foreign-city weather / crypto / malware | 0.98 | final check → refuse (or RAG on abort) |
| `severe_weather` | `_WEATHER_POLICY_RE` (T8, black rain, 風球…) | 1.0 | weather fact + live HKO + grounded compose |
| `fees` | `_FEE_RE` minus `_FEE_DURATION_RE` | 0.9 | `facts/fees.json` + grounded compose |
| `booking_tool` | `_AVAIL_STRONG_RE` / seat browse | 0.85–0.95 | CSV seats in **chat text** (+ tool gate) |
| `booking_dialogue` | book verbs + sticky / named programmes | ~0.85 | book FSM; assessment Yes → final check → SheetPro |
| `cancel_dialogue` | cancel verbs + sticky | 0.95 | cancel FSM; Yes → final check → cancellations sheet |
| `opening_hours` | `detect_opening_hours_intent` | 0.8 | calendar engine + optional HKO enrich |
| `unknown_programme` | catalogue miss | 0.95 | honest refuse + SheetPro (no final check) |
| `rag` | default / multi-topic | 0.5–0.65 | Chroma → Qwen (+ gap polish / SheetPro) |

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

- Typhoon / T8 / black rain → `severe_weather` (**grounded compose**; privileged policy/live doc; not pure RAG).
- “How much is phonics?” / `月費幾多？` / `幾錢一個月` → `fees` (card privileged; enrich OK).
- “Do you offer private 1:1?” → `rag`.
- “How much time does phonics take?” → not fees (`_FEE_DURATION_RE`) → RAG / other.
- Strong seat browse (`seats left`, `有冇位`, `仲有冇位`) → `booking_tool`, even if fee words co-occur.
- Bare waitlist (`候補`, `滿班`, `waiting list`) → `waiting`; bare `冇位` alone no longer implies waitlist.
- Safety “allergy + reaction” → `staff_escalation` before non-urgent `allergy` FAQ; final check fail-opens.
- Off-domain city weather → refuse path (+ final check); no fake SheetPro todo.
- Cancel confirm `no` / `do not cancel` → keep booked.

Probe script: `scripts/stress_intent_router.py` (50+ cases).

---

## 4. Structured tools

### 4.1 Centre facts + grounded compose

`backend/services/centre_facts.py` + `backend/facts/*.json` (baked into the SAE image; not the mutable `/app/data` volume).

Typical soft-FAQ flow:

1. Intent → `format_*_answer` → structured card text + citations.
2. If the route is in `_GROUNDING_ENRICH_ROUTES` and enrich is on → `retrieve_for_enrich` + `compose_grounded_fact` (card/live doc privileged; KB may elaborate).
3. Else / fallback → `adapt_structured_fact` rewrites **tone only** (`enable_thinking: false`).
4. Some routes intentionally skip adapt (e.g. `guest_policy`, hard FSM answers).

### 4.2 Opening hours

`detect_opening_hours_intent` → calendar engine (`OPENING_HOURS_ENABLED`):

1. Resolve datetime (explicit date, weekday name, or now).
2. Public holiday table → closed + name.
3. Sunday → closed.
4. Else weekday open/close window.
5. Optional HKO severe-weather hint (`OPENING_HOURS_WEATHER_ENABLED`) attached as a privileged live doc under grounded compose.

### 4.3 Seats, booking dialogue, cancel

CSV under `backend/data/sheets/`: teachers, windows, sessions, enrolments, trial_slots, families, cancellations.

- **`booking_tool`:** `seats_left = capacity − enrolments` → **chat answer**. Live register is privileged under grounded compose; an LLM **tool gate** may hide structured seat payloads (`ask_tool_ok`). Demo UI is WhatsApp-shaped: **no Book buttons / seat cards**.
- **`booking_dialogue`:** sticky FSM; identity from `phone` → `families.csv`. Resolves child → course → when → offers open seats as **bullet text**. Cheap LLM may fill missed course/when/teacher (`ARBITER_BOOKING_PARSE`). Named programmes open `confirm_assessment`; parent **yes** → **final check** → SheetPro `assessment_request`. Sticky state can yield to RAG when the parent leaves booking intent.
- **`cancel_dialogue`:** sticky FSM; list upcoming → confirm → **final check** on yes → `cancellations.csv`. Declines (`no` / `do not cancel`) keep the class. Ambiguous picks may use cheap LLM cancel pick (`ARBITER_CANCEL_PICK`).
- **`POST /bookings`:** REST helper that can append `chat_bookings.csv` for tooling/tests. The **demo parent chat does not commit seat bookings** that way — assessment hand-off is the live book story.

### 4.4 Staff surfaces

| Surface | Store | Trigger |
|---------|-------|---------|
| SheetPro follow-ups | `admin_todos` | unknown programme / soft RAG gap / assessment confirm (after final check) / outreach-promise guard |
| MailBox inbox | `staff_messages` | escalation after final check (safety fail-open) |
| Email draft | `staff_email` | cheap LLM polish of boss email; body kept for inbox UI |

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

`index_size`, `top_k`, `hit_count`, `top_score`, `embedding_mode`, `language_fallback`, `gap_reason`, `mode` (`pure_rag` | `grounded_enrich`), `hits[]` (full `text`).

Inspector shows hits when **pure RAG** owns the turn **or** when grounded enrich ran (`trace.grounding` / enrich mode). Structured tool citations alone must not look like KB chunks.

### 5.4 Soft gaps, off-KB refuse & guards

After pure RAG generate:

- If retrieval is **off-topic** (low token overlap with top hit) or the model flags `[[ADMIN_GAP: off_topic…]]`, replace the answer with a short **off-KB refuse** — do not lecture from world knowledge (homework / general science). No SheetPro row.
- Persona + system prompt: answer **only** from centre excerpts; refuse unrelated topics.
- Optional **gap polish** (`ARBITER_GAP_REWRITE`) rewrites empty/awkward soft refuses for true centre gaps.
- Soft gaps / unknown programmes write **SheetPro** rows (no final check).
- **Outreach guard:** if the model promises a staff callback without a todo, force a SheetPro row.
- Scrub accidental internal filenames (`admin_todos.csv`, etc.) from parent-facing copy.

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
| Main LLM | `LLM_MODEL` | code default `qwen-plus`; deploy example often `qwen3.8-flash` — RAG + compose/adapt |
| Cheap arbiter / final check / polish | `INTENT_ARBITER_MODEL` | falls back to `LLM_MODEL` |
| Embeddings | `EMBEDDING_MODEL` | `text-embedding-v4` |
| Arbiter master | `INTENT_ARBITER_ENABLED` | true (gates route arbiter, final check, polish helpers) |
| Arbiter timeout | `INTENT_ARBITER_TIMEOUT_S` | 12 |
| Adapt extras | `ARBITER_ADAPT_EXTRA` | true |
| Gap polish | `ARBITER_GAP_REWRITE` | true |
| Cancel pick | `ARBITER_CANCEL_PICK` | true |
| Booking parse | `ARBITER_BOOKING_PARSE` | true |
| Final check | `ARBITER_FINAL_CHECK` | true |
| Grounded enrich | `GROUNDING_ENRICH_ENABLED` / `GROUNDING_ENRICH_TOP_K` | true / 3 |
| Opening hours | `OPENING_HOURS_ENABLED` / `OPENING_HOURS_WEATHER_ENABLED` | true / true |
| Gen | `GEN_MAX_TOKENS` / `GEN_TEMPERATURE` | 280 / 0.3 |

---

## 8. Demo UI contract

**Left:** WhatsApp-shaped **parent chat only** (messages + composer). No Book buttons, no seat cards, no booking form.  
**Right — System inspector:**

1. Route pill + pipeline step rail (includes `final_check` when a commit ran)
2. One-tap **demo probes**
3. **Router decision** — match summary (highlighted cue) → path → expandable rule map + score ledger
4. **Answer grounding** — bypass / grounded compose / pure RAG
5. Hours / holiday / weather panel when relevant
6. RAG / enrich hits (rank, score, full chunk) when retrieve ran
7. Class register + session memory

**Right — Staff desk:** SheetPro follow-ups (formula bar shows full cell text) | MailBox Gmail-style inbox (`**bold`** rendered).

Canonical probes:

| Probe | Expected route | What to point at |
|-------|----------------|------------------|
| Typhoon | `severe_weather` | Match cue + grounded compose (privileged live/policy) |
| Phonics fee / `月費幾多？` | `rag` | Fee FAQ chunk rank / score / text (no fact card) |
| Address / FPS / teachers | `rag` | KB retrieve — static FAQs are RAG |
| Phonics seats / `仲有冇位？` | `booking_tool` | Chat seat counts + Class register |
| Book Jason | `booking_dialogue` | FSM → final check → SheetPro |
| Safety call | `staff_escalation` | final check (fail-open) + MailBox draft |

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
| **Grounded compose** | Soft FAQ path: privileged structured/live doc + KB enrich → compose |
| **Adapt** | Main-LLM tone rewrite of a structured fact; must not invent policy |
| **Route arbiter** | Cheap LLM that may keep an ambiguous route or send it to RAG |
| **Final check** | Cheap LLM gate before book / cancel / escalate / refuse commits |
| **Tool gate** | LLM flag on seat browse that may suppress exposing structured seat payloads |
| **Hard veto** | Ultra-high-confidence score that blocks soft prototype dilution |
| **RAG bypass** | Hard path skips KB retrieve on the answer; does not imply “no LLM” |
| **RAG** | Retrieve Markdown chunks from Chroma, then generate with citations |
| **Sticky dialogue** | Book/cancel FSM that stays active across turns for one `session_id` |
| **SheetPro / MailBox** | Staff-desk clones of Sheets follow-ups and Gmail urgent inbox |

---

## 11. Related

- [architecture.md](architecture.md) — compact diagram  
- [demo-script.md](demo-script.md) — 5-minute talk track  
- [DEVELOPER_SITE_INTEGRATION.md](DEVELOPER_SITE_INTEGRATION.md)  
- [../deploy/REDEPLOY.md](../deploy/REDEPLOY.md)
