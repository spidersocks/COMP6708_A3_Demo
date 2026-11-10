# Mega bombard issues

API: `http://47.242.157.72:8000` (pre-fix baseline, **v33**)  
Turns: **221** · OK: **169** · P0: **3** · P1: **11** · P2: **38**

## Issue frequency (baseline)

- `grounding_missing_on_rag`: 39
- `promise_outreach_without_sheet`: 5
- `route=rag expected_in=['booking_dialogue']`: 4
- `parent_leak` / echo of internal filenames: 2
- `admin_todo_missing`: 1 (LSE Yes turn)
- Misc routing mismatches: hours follow-up, zh book, soft callback, cancel confirm

## P0

1. **Little Sound Explorers booking dead-end** — `need_course` + programme name yielded to RAG, cleared booking; **Yes** promised outreach with **no SheetPro / MailBox row**.
2. **Parent-facing echo of internal files** — refusing `admin_todos` / `staff_messages.csv` still named those artifacts in the reply.
3. **Promise outreach without sheet** — RAG “team will reach out” / assessment arrange without `admin_todo`.

## P1

- Hours follow-up “What about tomorrow?” sometimes RAG instead of hours engine.
- Chinese book verb `想幫 Jason 報拼音班` missed booking dialogue.
- Soft “callback from the boss” missed hard escalation.
- Named programmes (Smart Spell) left booking mid-flow.
- Inspector: many pure-RAG turns lacked a `grounding` card.

## P2

- Soft grounding / route paraphrase noise (acceptable for demo).
- Multi-turn expect_route too strict on sibling-after-fees (sibling is correct).

## Fixes shipped (post-bombard → redeploy)

1. Map Little Sound Explorer / Smart Spell / Little Linguist / Language Arts → `phonics`; stay sticky at `need_course`.
2. New booking stage `confirm_assessment` → affirmative writes **`admin_todos`** (`assessment_request`).
3. RAG guardrail: outreach-promise answers always create SheetPro row; affirmative after assessment offer short-circuits to todo.
4. Pure RAG sets `trace.grounding.mode=pure_rag`; strip internal filename echoes; broaden off-domain + callback + ZH book detectors.

## Retest (v34) — all pass

- Jason → Little Sound Explorers → Yes → `booking_dialogue` / `confirm_assessment` / `assessment_queued` + `admin_todo` on SheetPro.
- Smart Spell → Yes → admin todo.
- ZH book, callback→staff_escalation, leak1/2→off_domain (no filename echo), RAG `grounding=pure_rag`.
- See `scripts/retest_v34_results.json`.
