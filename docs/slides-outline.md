# PowerPoint outline — COMP6708 Assignment 3

Suggested file name on submission: `Assignment3-GroupX.pptx` (plus this repo / zip as needed).

Keep slides sparse — the live system carries 70% of the grade.

---

## Slide 1 — Title

- **BrightNest Learning Centre Assistant**
- COMP6708 Mini Demo · Group X
- Tagline: Grounded after-hours help for parents (RAG + booking tools)

## Slide 2 — Problem & scenario

- HK bilingual learning centres get repetitive WhatsApp/parent questions after hours
- Staff cannot answer 24/7; wrong answers about fees/hours are costly
- Opportunity: LLM assistant grounded in centre knowledge + soft booking

## Slide 3 — Technique choice: RAG (+ light tools)

- Why RAG (not fine-tuning): docs change weekly; training-free; citations
- Pipeline: chunk markdown → embed (DashScope) → Chroma retrieve → Qwen generate
- Tools: opening hours, availability, booking (structured, demo-safe)

## Slide 4 — Architecture

Diagram (also in README):

```
UI (OSS/CDN) → FastAPI (SAE/ECS) → RAG (Chroma) → DashScope Qwen
                              ↘ booking SQLite
                              ↘ content/*.md
```

## Slide 5 — Implementation highlights

- Isolated monorepo (not production tutoring stack)
- Self-built RAG (not AWS Bedrock Knowledge Base)
- Multilingual KB (en / zh-HK / zh-CN)
- UI shows **sources** + **bookable slots**

## Slide 6 — Impact & evaluation (qualitative)

- Faster parent triage; fewer missed after-hours leads
- Groundedness via citations; fallback when offline
- Demo metrics you can mention: index size, top-k, latency feel

## Slide 7 — Thank you / Q&A

- Repo + live URL
- Contact / group members
