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
6. Cold-rehearse probes: **Typhoon → Phonics fee → RAG · Private 1:1 → Phonics seats → Book Jason → Unknown ask → Safety call**.

---

## Minute-by-minute (say / show)

### 0:00–0:40 · Open + problem (slides: Title → One sentence → Problem)

**Show:** Live UI (parent chat + system inspector). Title slide only if walking in.

**Narrator (≈35s) — tutoring-centre framing:**
> Imagine you run a bilingual children's tutoring centre in Hong Kong. After hours, parents always ask a lot of questions — how much is it for phonics lessons? are you open if tomorrow has a typhoon? are there spaces on saturday? can I book a trial? But here's the problem. Admin staff cannot reply to messages 24/7. If you take too long to reply, you frustrate parents. But if you give a wrong fee or hours answer, you lose trust. So to solve your problem, you hired us, Group 15, and a couple of weeks later, we built **BrightNest**: an assistant that answers from centre truth, acts with tools, and hands off when a human must.

**Pilot:** Cursor on demo probes. Do not click yet.

---

### 0:40–1:20 · Technique (slide: Technique / Architecture — flash ≤20s)

**Show:** Technique slide briefly, then Architecture, then **back to live UI**.

**Narrator (technical — this is Advanced Big Data Computing):**
> Training-free on purpose. Centre documents change every week, so we do **not** fine-tune a model. We use **RAG** — chunk our own markdown KB, embed with DashScope, store in Chroma, retrieve top-k, generate with Qwen. On top sits a **hybrid intent router**. Important: the router’s **scores are not retrieval similarities** — hard detectors award fixed numbers (allergy 0.95, fees 0.90, typhoon 1.0); you can open the **score ledger** in the inspector and see every award, blend, and conflict clear. Soft FAQ routes then do **grounded compose**: structured fact card stays privileged, KB chunks may enrich. Hard FSM paths — booking dialogue, cancel, escalation — **bypass** retrieve. The parent surface is WhatsApp-shaped: **chat only**, no fake booking form. Self-built pipeline, SAE + public web.

**Pilot:** Optional 2-second point at Architecture, then live UI.

---

### 1:20–1:55 · Beat 1 — Typhoon = structured tool (not pure RAG)

**Show:** Probe **Typhoon**.

**Say:**
> Watch **Router decision**. Severe-weather fires at score 1.0 from hard_regex. We still **retrieve** general weather advice from the KB, but a **live HKO / policy doc is privileged** — Answer grounding shows grounded compose. The model may elaborate; it must not invent a different signal.

**Point at:** Match cue · grounding (live doc + enrich) · RAG enrichment panel · score ledger.

**Backup:** Christmas → opening-hours engine / public holiday.

---

### 1:55–2:35 · Beat 2 — Fees tool, then real RAG

**Show A:** Probe **Phonics fee**.

**Say:**
> “How much is phonics?” is a **fee-guide** route. Open **Intent scores** — `fees=0.90` from `hard_regex`, not from Chroma. Then check **Answer grounding**: soft routes **retrieve KB as enrichment** while the fee card stays privileged — the model may elaborate, but must not invent tuition over the card.

**Show B (immediately):** Probe **RAG · Private 1:1**.

**Say:**
> Now expand **RAG retrieval**. Pure retrieve→generate: index size, top-k, hit ranks, retrieval scores — click a chunk for the **full** text. Contrast with fees: there the card wins; here RAG owns the answer.

**Point at:** Rank #1 score · Expand full chunk · sources under the answer.

**Backup:** Teachers / Location (structured) if RAG is slow.

---

### 2:35–3:20 · Beat 3 — Live seats + WhatsApp booking

**Show A:** Probe **Phonics seats**.

**Say:**
> Seat counts come from the admin **Class register** — CSV-backed — the model is not inventing capacity. The answer stays in the chat, the way WhatsApp would.

**Pilot:** Expand **Class register** if useful (opens on booking-related routes).

**Show B (if time):** Probe **Book Jason**.

**Say:**
> Multi-turn booking dialogue — sticky FSM, identity from the messaging number. If the parent names a programme like Little Sound Explorer, we confirm a diagnostic assessment and write a quiet **SheetPro** follow-up — we never promise outreach without a staff row.

**Backup:** Cancel class · skip assessment demo if behind.

---

### 3:20–4:00 · Beat 4 — Soft gap → Follow-up sheet

**Show:** Probe **Unknown ask** (scuba). Switch **Staff desk → SheetPro**.

**Say:**
> We do not invent programmes we do not offer. Parent gets an honest refuse; staff get a Sheets-style follow-up row — quiet research queue, not a hallucination.

**Point at:** New open row · human reason label.

---

### 4:00–4:40 · Beat 5 — Safety → Gmail inbox

**Show:** Probe **Safety call**. Switch **MailBox**.

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
3. **Technique:** “Training-free RAG plus a hybrid router — scores are detector awards, not retrieval similarities.”
4. **Typhoon:** “Score 1.0 hard_regex — live policy privileged — KB may enrich — no invented signal.”
5. **RAG:** “Expand the chunk — rank, score, full text — that is our retrieve then generate.”
6. **Seats / book:** “Live register in chat — book by conversation, not a fake form — assessment Yes lands on SheetPro.”
7. **Safety:** “When a child may be hurt, we escalate — we do not chat forever.”
8. **Close:** “Grounded answers. Structured actions. Honest hand-offs.”

---

## If something fails

| Failure | Instant move |
|---------|----------------|
| API offline | Spotter: hotspot / localhost; Narrator covers architecture 20s |
| Wrong route | Open **Router decision** — narrate which rule fired; probe **Friend guest** (fast structured) |
| Typhoon looks “soft” | Open **Answer grounding** — narrate privileged live/policy doc |
| Slow RAG | Narrator: “DashScope generate — watch the step rail” |
| Empty inbox | Re-send **Safety call**; refresh Staff desk |
| Booking stalls | Stay on chat copy + Class register; skip assessment follow-up |

---

## What not to do

- Do not claim phonics fee is RAG — it is the fees tool.
- Do not look for Book buttons or a Booking tool panel — removed on purpose (WhatsApp).
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
