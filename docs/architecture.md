# Architecture notes

```mermaid
flowchart TB
  UI[BrightNest React UI]
  API[FastAPI backend]
  OH[Opening hours tool]
  BK[Availability and booking]
  ESC[Staff escalation]
  MSG[staff_messages inbox]
  RAG[RAG pipeline]
  CH[Chroma store]
  DS[DashScope Qwen]
  MD[content markdown KB]

  UI -->|POST /chat| API
  UI -->|POST /bookings| BK
  UI -->|GET /admin/messages| MSG
  API --> OH
  API --> BK
  API --> ESC
  ESC --> MSG
  API --> RAG
  RAG --> CH
  RAG --> DS
  MD -->|ingest| CH
```

## Staff escalation

Before RAG, the router detects ask-for-human / anger / safety cues (`services/escalation.py`). Parents get a calm acknowledgement (no mention of internal sheets). Staff see a row under **Admin → Messages** (`services/staff_messages.py`). Soft KB gaps still use the separate admin to-do path.

## RAG details

1. **Ingest** (`rag/ingest.py`): parse frontmatter + body, chunk ~900 chars with overlap, embed, upsert into Chroma.
2. **Retrieve** (`rag/pipeline.py`): embed query, cosine top-k, optional `language` metadata filter with unfiltered retry.
3. **Generate** (`rag/generator.py`): prompt with persona + excerpts; DashScope chat completions; extractive fallback if no key.

## Why not Bedrock KB?

Assignment emphasis is understanding the technique. Implementing chunk/embed/retrieve/generate in-repo makes the design auditable for the live demo and slides.
