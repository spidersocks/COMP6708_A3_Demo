# 5-minute live demo script — BrightNest

**Total target: ≤ 5:00.** Operate the system; slides are supporting only.

## Pre-flight (before you walk up)

1. Backend running (`uvicorn` or Alibaba SAE); `GET /healthz` OK
2. Frontend open full-screen; Reset chat once
3. Optional: DashScope key set (otherwise mention local-fallback mode briefly)
4. Have phone hotspot ready if campus Wi-Fi fails → localhost backup

## Minute-by-minute

| Time | Action | Say |
|------|--------|-----|
| 0:00–0:40 | Title slide + open UI | “Parents of a bilingual centre need after-hours answers. We built BrightNest Assistant with **self-built RAG** on Alibaba DashScope.” |
| 0:40–1:20 | Chip: **opening hours** | Show deterministic hours answer. “Operational facts can be tools, not only LLM guesses.” |
| 1:20–2:20 | Chip: **How much is phonics?** | Expand **Sources used**. “Retrieve from our markdown KB, then generate — we implemented the pipeline, not a managed black-box KB.” |
| 2:20–3:10 | Chip: **Show trial class availability** | Point at slot cards. “Availability is a structured tool over a seeded timetable.” |
| 3:10–4:00 | Tap **Book** on a slot | Show confirmation + booking id. “Provisional booking, no payment — good for after-hours capture.” |
| 4:00–4:30 | Optional: one zh-HK question e.g. `營業時間` or `學費` | “Same stack, language-aware retrieval.” |
| 4:30–4:45 | Optional: “I want to speak to a manager” → open Admin → Messages | “Escalation is a first-class route — staff inbox, not just more RAG.” |
| 4:45–5:00 | Architecture slide | “Impact: 24/7 parent triage + grounded answers + booking + human hand-off.” |

## Backup questions if something fails

- “Where are you located?”
- “Tell me about your teachers”
- “What can you help with?”

## What not to do

- Don’t read slide bullets aloud for more than ~20s
- Don’t dig into AWS/production tutoring history
- Don’t invent real customer names or real centre secrets (this brand is fictional)
