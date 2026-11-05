# BrightNest — 5-minute live demo script (Group 15)

**Assignment rule:** the five minutes are for *operating the system*. Slides are chapter cards only.  
**Live URL:** https://seanfontaine.dev/COMP6708_A3_Demo  
**Target:** ≤ 5:00 · speak ~110–130 words/minute · never read slide bullets.  
**System truth:** [TEAM.md](TEAM.md) — if the UI disagrees with this script, the system wins; fix the script.

---

## Roles (suggested)

| Role | Who | Job |
|------|-----|-----|
| **Pilot** | Sean (or strongest demo operator) | Clicks, Reset, Staff desk, expand inspector |
| **Narrator** | Second speaker | Problem → technique → impact |
| **Spotter** | Third | Watches route pill; cues backup probe |
| **Closer** | Fourth | Closing line + team names if asked |

---

## Pre-flight (T−10 min)

1. Open demo **fullscreen** on the projector laptop.
2. Confirm green **API online**.
3. Hit **Reset** once (clean chat + desk).
4. Slides on a second screen / phone — **do not** fullscreen slides over the demo.
5. Spotter: hotspot ready if Wi-Fi dies → localhost backup.
6. Cold-rehearse probes: **Typhoon → Phonics fee → RAG · Private 1:1 → Phonics seats → Unknown ask → Safety call**.

---

## Minute-by-minute (say / show)

### 0:00–0:40 · Open + problem (slides: Title → One sentence → Problem)

**Show:** Live UI (parent chat + system inspector). Title slide only if walking in.

**Narrator (≈35s) — tutoring-centre framing:**
> Imagine you run a bilingual learning centre in Hong Kong. After hours, parents keep messaging the same things — *are we open in a typhoon? how much is phonics? are there seats on Saturday? can I book a trial?* Staff cannot live on WhatsApp. A wrong fee or hours answer costs trust. So you hired us — Group 15. A couple of weeks later, we built **BrightNest**: an assistant that answers from centre truth, acts with tools, and hands off when a human must.

**Pilot:** Cursor on demo probes. Do not click yet.

---

### 0:40–1:20 · Technique (slide: Technique / Architecture — flash ≤20s)

**Show:** Technique slide briefly, then Architecture, then **back to live UI**.

**Narrator (technical — this is Advanced Big Data Computing):**
> Training-free on purpose. Centre documents change every week, so we do **not** fine-tune a model. We use **RAG** — retrieve-augmented generation: chunk our own markdown knowledge base, embed with DashScope, store in Chroma, retrieve top-k by similarity, then generate with Qwen. On top of that sits a **hybrid intent router**: hard regex / structured rules for typhoons, fees, and seats; a soft prototype blend for paraphrase; and a cheap LLM review pass only when confidence is low. High-confidence structured picks skip the arbiter — you will see that in the inspector. Self-built pipeline, deployed on Alibaba SAE, UI on the public web.

**Pilot:** Optional 2-second point at Architecture, then live UI.

---

### 1:20–1:55 · Beat 1 — Typhoon = structured tool (not RAG)

**Show:** Probe **Typhoon**.

**Say:**
> Watch **Router decision**. The severe-weather rule fires at score 1.0. Cheap LLM review is **skipped** — high confidence. RAG is **skipped** on purpose. We load the centre typhoon policy as a structured fact, then the main LLM does a **fluency rewrite** so it sounds like a human, without inventing policy.

**Point at:** Router path · rule map (`severe_weather` fired, `rag` idle) · adapt = yes · RAG panel says skipped.

**Backup:** Christmas → opening-hours engine / public holiday.

---

### 1:55–2:35 · Beat 2 — Fees tool, then real RAG

**Show A:** Probe **Phonics fee**.

**Say:**
> “How much is phonics?” is a **fee-guide tool**, not RAG — published numbers must not be guessed from a random chunk.

**Show B (immediately):** Probe **RAG · Private 1:1**.

**Say:**
> Now expand **RAG retrieval**. Index size, top-k, hit ranks, retrieval scores — click a chunk to open the **full** retrieved text. That is our chunk → embed → retrieve → generate path, not a managed black-box KB.

**Point at:** Rank #1 score · Expand full chunk · sources under the answer.

**Backup:** Teachers / Location (structured) if RAG is slow.

---

### 2:35–3:20 · Beat 3 — Live seats + booking

**Show:** Probe **Phonics seats**.

**Say:**
> Seat counts come from the admin register — CSV-backed tool — the model is not inventing capacity. Here are bookable cards.

**Pilot:** Tap **Book** on one card if time; show confirmation id. Flash class register if useful.

**Say (if book):**
> Provisional after-hours capture — no payment — staff confirm later.

**Backup:** Trial slots · Book Jason.

---

### 3:20–4:00 · Beat 4 — Soft gap → Follow-up sheet

**Show:** Probe **Unknown ask** (scuba). Switch **Staff desk → Follow-up sheet**.

**Say:**
> We do not invent programmes we do not offer. Parent gets an honest refuse; staff get a Sheets-style follow-up row — quiet research queue, not a hallucination.

**Point at:** New open row · human reason label.

---

### 4:00–4:40 · Beat 5 — Safety → Gmail inbox

**Show:** Probe **Safety call**. Switch **Staff inbox**.

**Say:**
> Allergy reaction after class — urgent. Parent hears a safety-aware reply. Staff get a Gmail-style draft: call now, child name, phone, quoted message.

**Point at:** Subject “Urgent…”, BN avatar, email body CTA.

**Optional (5s):** Off-topic Tokyo weather — fast refuse, **no** fake todo.

---

### 4:40–5:00 · Close

**Show:** Live UI still visible.

**Closer:**
> Grounded answers. Structured actions. Honest hand-offs. BrightNest — Group 15, PolyU Computing. Happy to take questions.

---

## Lines worth memorising

1. **Problem:** “Imagine you run a tutoring centre — parents ask the same things after hours; staff cannot live on WhatsApp.”
2. **Hire joke:** “So you hired us — Group 15 — and a couple of weeks later we built BrightNest.”
3. **Technique:** “Training-free RAG plus a hybrid router so typhoons and seats are tools, not guesses.”
4. **Typhoon:** “Rule fires at 1.0 — arbiter skipped — RAG skipped — policy plus fluency rewrite.”
5. **RAG:** “Expand the chunk — rank, score, full text — that is our retrieve then generate.”
6. **Seats:** “Live register — not the model inventing capacity.”
7. **Safety:** “When a child may be hurt, we escalate — we do not chat forever.”
8. **Close:** “Grounded answers. Structured actions. Honest hand-offs.”

---

## If something fails

| Failure | Instant move |
|---------|----------------|
| API offline | Spotter: hotspot / localhost; Narrator covers architecture 20s |
| Wrong route | Open **Router decision** — narrate which rule fired; probe **Friend guest** (fast structured) |
| Typhoon “no RAG” | That is correct — explain skip; switch to **RAG · Private 1:1** |
| Slow RAG | Narrator: “DashScope generate — watch the step rail” |
| Empty inbox | Re-send **Safety call**; refresh Staff desk |
| Booking fail | Stay on seat list; skip Book |

---

## What not to do

- Do not claim phonics fee is RAG — it is the fees tool.
- Do not read PowerPoint for more than ~20 seconds total.
- Do not apologise at length for latency — narrate the pipeline.
- Do not mention real customer data (BrightNest is fictional).
- Do not open `.env` or keys on screen.

---

## Assignment checklist (verbal)

| Requirement | Where you hit it |
|-------------|------------------|
| Training-free LLM technique (RAG) | Technique + Beat 2B |
| Interactive UI live | Entire demo |
| Problem + scenario | 0:00–0:40 |
| Why this technique | 0:40–1:20 |
| Key implementation | Router + RAG inspector |
| Main features | Beats 1–5 |
| Impact / evaluation | Close + “no formal study” if asked |

---

## Submission naming

Package as **`Assignment3-Group15`** (PDF slides + link/repo as required).
