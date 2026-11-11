# BrightNest KB schema

Parent-intent folders (each language: `en` / `zh-HK` / `zh-CN`).

| Folder | What belongs here | Count (EN) |
|--------|-------------------|------------|
| `centre` | Who we are, where, hours, contact, teachers, facilities | 9 |
| `programmes` | Course catalog + curriculum detail / comparisons | 33 |
| `age_bands` | “What for my X-year-old?” recommendation cards | 10 |
| `fees` | Prices, discounts, deposits, how to pay, worksheet sales | 6 |
| `admissions` | Trial, assessment, waitlist, enrollment, timetable windows | 7 |
| `attendance` | Makeup, freeze, pickup, late arrival, parent stay | 8 |
| `policies` | Rules: refunds, weather, pets, allergy, dress, homework, materials | 10 |
| `classroom` | In-class experience: bilingual, size, progress, observation, birthdays | 8 |
| `assistant` | Bot voice / routing only (greetings, silence rules, escalation) — not brochure copy | 11 |
| `marketing` | Promotions, testimonials, why-choose | 3 |
| `complaint` | Complaint response template | 1 |

**Total: 106 docs × 3 languages.**

## Frontmatter

```yaml
---
language: en
type: fees          # matches folder role (programme, age_band, policy, …)
canonical: FeesOverview
folder: fees        # must match the directory name
aliases: fee; tuition; …
---
```

## Retrieval notes

- Keep policy is **channel pack**: dense ranks 1–3 for paraphrase, then up to 2 BM25 supplements for exact terms.
- Age questions also inject `age_bands/age_*.md` via `_age_faq_overlay`.
- `assistant/` docs are operational guidance for the model; prefer keeping them short and rule-like.

## History

Previously almost everything sat in a catch-all `faq/` (73) beside `courses` / `policies` / `institution`. Reorganized 2026-11-11 — see `scripts/reorganize_kb.py`.
