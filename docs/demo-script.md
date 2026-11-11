# BrightNest — 5-minute live demo script (Group 15)

**Live:** https://seanfontaine.dev/COMP6708_A3_Demo  
**Grade weight:** operating the system (~70%). Slides = chapter cards only.  
**Truth source:** [TEAM.md](TEAM.md). If UI and script disagree, fix the script.

**Pace:** ≤ 5:00 · ~110–130 wpm · never read bullets aloud.

---

## Roles

| Role | Job |
|------|-----|
| **Pilot** | Reset, probes, Staff desk, expand inspector |
| **Narrator** | Problem → technique → each beat’s “why” |
| **Spotter** | Route pill; backup probe; hotspot if Wi-Fi dies |
| **Closer** | Tagline + Q&A hand-off |

---

## Pre-flight (T−10)

1. Demo **fullscreen** on the projector machine; green **API online**.
2. **Reset** once.
3. Slides on a second screen — never over the live UI.
4. Cold run: Typhoon → Phonics fee → RAG · Private 1:1 → Phonics seats → Book Jason → Unknown ask → Safety call.

---

## Minute-by-minute

### 0:00–0:40 · Problem (Title → One sentence → Problem)

**Show:** Live UI (parent chat + inspector).

**Narrator:**
> Imagine you run a bilingual children’s centre in Hong Kong. After hours, parents still ask: How much is phonics? Are you open in a typhoon? Any Saturday seats? Can I book a trial? Staff cannot live on WhatsApp 24/7 — slow replies frustrate parents; a wrong fee or hours answer kills trust. You hired Group 15. A few weeks later we shipped **BrightNest**: answers from centre truth, actions through tools, and honest hand-offs when a human must decide.

**Pilot:** Cursor on probes — do not click yet.

---

### 0:40–1:20 · Technique (Technique / Architecture ≤20s, then live)

**Narrator:**
> This is a **training-free** system on purpose — centre docs change weekly, so fine-tuning is the wrong loop. We built our own **RAG** pipeline: markdown KB → DashScope embeddings → Chroma ∥ BM25 → channel pack (3 dense + 2 BM25) → Qwen with citations. Above that sits a **hybrid intent router**. The scores you see are **not** retrieval similarities — hard detectors award fixed confidence (typhoon 1.0, fees 0.90). Soft FAQ routes **grounded-compose**: the fact card stays privileged; KB may enrich. Hard paths — book, cancel, escalate, refuse — **bypass retrieve** so live seats and safety copy cannot be diluted. Before those commits, a **cheap LLM final check** must go ahead, clarify, or abort. Parent chat stays WhatsApp-shaped: conversation only.

**Pilot:** Flash Architecture; return to live UI.

---

### 1:20–1:55 · Beat 1 — Typhoon (structured + enrich)

**Show:** Probe **Typhoon**.

**Say:**
> Router decision: severe-weather at score **1.0** from `hard_regex`. Answer grounding shows **grounded compose** — live HKO / policy doc privileged; KB may add general advice. The model elaborates; it must not invent a different signal.

**Point:** Match cue · grounding · enrich panel · score ledger.

**Backup:** Christmas → opening-hours / public holiday.

---

### 1:55–2:35 · Beat 2 — Fees tool, then pure RAG

**Show A:** **Phonics fee**.

**Say:**
> Fee-guide route. Intent scores: `fees=0.90` from the detector ledger — not from Chroma. Grounding: card privileged, KB enrich allowed.

**Show B:** **RAG · Private 1:1**.

**Say:**
> Now expand **RAG retrieval** — index size, top-k, rank, retrieval score, full chunk. Contrast with fees: there the card wins; here retrieve→generate owns the answer.

**Backup:** Teachers / Location if RAG is slow.

---

### 2:35–3:20 · Beat 3 — Seats + conversational booking

**Show A:** **Phonics seats**.

**Say:**
> Seat counts come from the live **Class register** (CSV) — not model invention. Copy stays in chat, the way WhatsApp would.

**Show B (if time):** **Book Jason**.

**Say:**
> Sticky booking dialogue from the messaging number. For a named programme we ask for a diagnostic assessment; when the parent says **yes**, watch the inspector — **LLM final check · book** → then a quiet **SheetPro** row. No fake booking form.

**Backup:** Cancel class (decline with **no** → Kept as booked).

---

### 3:20–4:00 · Beat 4 — Soft gap → SheetPro

**Show:** **Unknown ask** (scuba). Staff desk → **SheetPro**.

**Say:**
> We do not invent programmes we do not teach. Parent hears an honest gap; staff get a Sheets-style research row — capture the lead, refuse the hallucination.

---

### 4:00–4:40 · Beat 5 — Safety → MailBox

**Show:** **Safety call**. Staff desk → **MailBox**.

**Say:**
> Suspected allergy reaction — escalate. Parent gets safety-aware copy; staff get a Gmail-style draft with call-now CTA. Final check still runs, but **safety fail-opens** so the inbox is never blocked on the model.

**Optional (5s):** Tokyo weather — refuse path; final check can clarify or send to RAG instead of a brittle hard refuse.

---

### 4:40–5:00 · Close

**Closer:**
> Grounded answers. Structured actions. Honest hand-offs. BrightNest — Group 15, PolyU Computing. Happy to take questions.

---

## Lines worth memorising

1. “Parents ask after hours; staff cannot live on WhatsApp — wrong fees kill trust.”
2. “Training-free RAG + hybrid router — scores are detector awards, not retrieval sim.”
3. “Hard paths bypass retrieve; a cheap LLM still gives the final go-ahead before commit.”
4. “Typhoon: score 1.0 — live policy privileged — KB may enrich.”
5. “Expand the chunk — that is retrieve then generate.”
6. “Seats from the register; book in chat; Yes → final check → SheetPro.”
7. “When a child may be hurt, we escalate — we do not chat forever.”
8. “Grounded answers. Structured actions. Honest hand-offs.”

---

## If something fails

| Failure | Instant move |
|---------|----------------|
| API offline | Hotspot / localhost; Narrator covers architecture 20s |
| Wrong route | Open Router decision; narrate the rule; probe **Friend guest** |
| Typhoon looks soft | Open Answer grounding — privileged live/policy doc |
| Slow RAG | “DashScope generate — watch the step rail” |
| Empty inbox | Re-send **Safety call**; refresh Staff desk |
| Booking stalls | Chat seats + Class register; skip assessment if behind |

---

## What not to do

- Do not call phonics fee “RAG” — it is the fees tool.
- Do not hunt for Book buttons — chat only by design.
- Do not read slides for more than ~20s total.
- Do not apologise for latency — narrate the pipeline.
- Do not show `.env` or keys.

---

## Assignment map (hit these verbally)

| Rubric idea | Where |
|-------------|-------|
| Training-free LLM technique (RAG) | Technique + Beat 2B |
| Interactive live UI | Entire demo |
| Problem + scenario | 0:00–0:40 |
| Why this technique | 0:40–1:20 |
| Key implementation | Router + final check + RAG inspector |
| Main features | Beats 1–5 |
| Impact / evaluation | Close · “live path correctness, visible RAG, latency feel” |

---

## Submission

Package as **`Assignment3-Group15`** (PDF slides + live link / repo as required).
