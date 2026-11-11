"""
Reorganize BrightNest KB from catch-all faq/ into parent-intent folders.

Old: faq · courses · policies · institution · marketing · guides · complaint
New: centre · programmes · age_bands · fees · admissions · attendance
     · policies · classroom · assistant · marketing · complaint
"""
from __future__ import annotations

import re
import shutil
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "content"
LANGS = ("en", "zh-HK", "zh-CN")

# dest_folder -> type label written into frontmatter
FOLDER_TYPE = {
    "centre": "centre",
    "programmes": "programme",
    "age_bands": "age_band",
    "fees": "fees",
    "admissions": "admissions",
    "attendance": "attendance",
    "policies": "policy",
    "classroom": "classroom",
    "assistant": "assistant",
    "marketing": "marketing",
    "complaint": "complaint",
}

# relative path under lang/ -> (new_folder, new_filename_or_subdir)
# Keys use forward slashes. Values: (folder, relative path within folder)
MOVE: dict[str, tuple[str, str]] = {
    # --- centre (about the place) ---
    "institution/intro.md": ("centre", "intro.md"),
    "institution/location.md": ("centre", "location.md"),
    "institution/contact.md": ("centre", "contact.md"),
    "institution/opening_hours.md": ("centre", "opening_hours.md"),
    "institution/registered_school.md": ("centre", "registered_school.md"),
    "institution/teachers.md": ("centre", "teachers.md"),
    "faq/getting_here.md": ("centre", "getting_here.md"),
    "faq/centre_facilities.md": ("centre", "facilities.md"),
    "faq/term_break_hours.md": ("centre", "term_break_hours.md"),
    # --- programmes (catalog + curriculum detail) ---
    "courses/_catalog.md": ("programmes", "_catalog.md"),
    "courses/Alludio.md": ("programmes", "Alludio.md"),
    "courses/ap_chinese.md": ("programmes", "ap_chinese.md"),
    "courses/chinese_programs.md": ("programmes", "chinese_programs.md"),
    "courses/ChineseLanguageArts.md": ("programmes", "ChineseLanguageArts.md"),
    "courses/ChineseWritingPrograms.md": ("programmes", "ChineseWritingPrograms.md"),
    "courses/Clevercal.md": ("programmes", "Clevercal.md"),
    "courses/EnglishPrograms.md": ("programmes", "EnglishPrograms.md"),
    "courses/LanguageArts.md": ("programmes", "LanguageArts.md"),
    "courses/MandarinPinyin.md": ("programmes", "MandarinPinyin.md"),
    "courses/Phonics.md": ("programmes", "Phonics.md"),
    "courses/Playgroups.md": ("programmes", "Playgroups.md"),
    "courses/PrivateClass.md": ("programmes", "PrivateClass.md"),
    "courses/ToddlerCharRecognition.md": ("programmes", "ToddlerCharRecognition.md"),
    "faq/adult_classes.md": ("programmes", "adult_classes.md"),
    "faq/brush_up_class.md": ("programmes", "brush_up_class.md"),
    "faq/cantonese_course.md": ("programmes", "cantonese_course.md"),
    "faq/ChineseWritingPrograms.md": ("programmes", "chinese_writing_faq.md"),
    "faq/gapsk.md": ("programmes", "gapsk.md"),
    "faq/holiday_vs_regular.md": ("programmes", "holiday_vs_regular.md"),
    "faq/interview_recitation.md": ("programmes", "interview_recitation.md"),
    "faq/little_linguist_overview.md": ("programmes", "little_linguist_overview.md"),
    "faq/mandarin_pinyin_in_regular_chinese.md": ("programmes", "mandarin_pinyin_in_regular_chinese.md"),
    "faq/mandarin_program_overview.md": ("programmes", "mandarin_program_overview.md"),
    "faq/math_faq.md": ("programmes", "math_faq.md"),
    "faq/phonics_duration.md": ("programmes", "phonics_duration.md"),
    "faq/phonics_level_outcomes.md": ("programmes", "phonics_level_outcomes.md"),
    "faq/phonics_level_outcomes_summary.md": ("programmes", "phonics_level_outcomes_summary.md"),
    "faq/reading_assignments_purpose.md": ("programmes", "reading_assignments_purpose.md"),
    "faq/smartspell_stages.md": ("programmes", "smartspell_stages.md"),
    "faq/smartspell_writing_milestones.md": ("programmes", "smartspell_writing_milestones.md"),
    "faq/sound_explorers_vs_smartspell.md": ("programmes", "sound_explorers_vs_smartspell.md"),
    "faq/summer_holiday_courses.md": ("programmes", "summer_holiday_courses.md"),
    # --- age bands ---
    "faq/age/age_18-28_months.md": ("age_bands", "age_18-28_months.md"),
    "faq/age/age_28m-3y.md": ("age_bands", "age_28m-3y.md"),
    "faq/age/age_3-4_k1.md": ("age_bands", "age_3-4_k1.md"),
    "faq/age/age_4-5_k2.md": ("age_bands", "age_4-5_k2.md"),
    "faq/age/age_5-6_k3.md": ("age_bands", "age_5-6_k3.md"),
    "faq/age/age_6-7_p1.md": ("age_bands", "age_6-7_p1.md"),
    "faq/age/age_7-8_p2.md": ("age_bands", "age_7-8_p2.md"),
    "faq/age/age_8-9_p3.md": ("age_bands", "age_8-9_p3.md"),
    "faq/age/age_9-10_p4.md": ("age_bands", "age_9-10_p4.md"),
    "faq/age/age_10-12_p5-p6.md": ("age_bands", "age_10-12_p5-p6.md"),
    # --- fees ---
    "faq/fees_overview.md": ("fees", "fees_overview.md"),
    "faq/tuition_enquiry.md": ("fees", "tuition_enquiry.md"),
    "faq/sibling_family_discount.md": ("fees", "sibling_family_discount.md"),
    "faq/registration_fees_deposit.md": ("fees", "registration_fees_deposit.md"),
    "faq/worksheets_sale.md": ("fees", "worksheets_sale.md"),
    "guides/how_to_pay.md": ("fees", "how_to_pay.md"),
    # --- admissions ---
    "courses/trial_assessment.md": ("admissions", "trial_assessment.md"),
    "faq/trial_class.md": ("admissions", "trial_class.md"),
    "faq/assessments_faq.md": ("admissions", "assessments_faq.md"),
    "faq/waiting_list.md": ("admissions", "waiting_list.md"),
    "faq/sample_timetable_windows.md": ("admissions", "sample_timetable_windows.md"),
    "policies/enrollment.md": ("admissions", "enrollment.md"),
    "policies/enrollment_form.md": ("admissions", "enrollment_form.md"),
    # --- attendance ---
    "policies/absence_makeup.md": ("attendance", "absence_makeup.md"),
    "faq/makeup_lessons_necessary.md": ("attendance", "makeup_lessons_necessary.md"),
    "faq/makeup_quota_examples.md": ("attendance", "makeup_quota_examples.md"),
    "faq/package_freeze_hold.md": ("attendance", "package_freeze_hold.md"),
    "faq/dropoff_pickup.md": ("attendance", "dropoff_pickup.md"),
    "faq/authorized_pickup.md": ("attendance", "authorized_pickup.md"),
    "faq/parent_attendance.md": ("attendance", "parent_attendance.md"),
    "faq/late_arrival_policy.md": ("attendance", "late_arrival.md"),
    # --- policies (rules) ---
    "policies/refund_transfer.md": ("policies", "refund_transfer.md"),
    "policies/severe_weather.md": ("policies", "severe_weather.md"),
    "policies/pets.md": ("policies", "pets.md"),
    "policies/teaching_material_policy.md": ("policies", "teaching_material_policy.md"),
    "policies/blooket_instructions.md": ("policies", "blooket_instructions.md"),
    "faq/discontinuing_and_refunds.md": ("policies", "discontinuing_and_refunds.md"),
    "faq/allergy_safety.md": ("policies", "allergy_safety.md"),
    "faq/dress_code_uniform.md": ("policies", "dress_code_uniform.md"),
    "faq/online_classes_livestream.md": ("policies", "online_classes_livestream.md"),
    "faq/homework_policy.md": ("policies", "homework_policy.md"),
    # --- classroom (in-class experience) ---
    "faq/bilingual_classroom.md": ("classroom", "bilingual_classroom.md"),
    "faq/class_size.md": ("classroom", "class_size.md"),
    "faq/class_observation_visits.md": ("classroom", "class_observation_visits.md"),
    "faq/child_progress.md": ("classroom", "child_progress.md"),
    "faq/changing_teachers.md": ("classroom", "changing_teachers.md"),
    "faq/birthdays_celebrations.md": ("classroom", "birthdays_celebrations.md"),
    "faq/what_to_bring.md": ("classroom", "what_to_bring.md"),
    "faq/parent_admin_qna_examples.md": ("classroom", "parent_admin_qna_examples.md"),
    # --- assistant (bot voice / routing — not parent brochure copy) ---
    "faq/greeting.md": ("assistant", "greeting.md"),
    "faq/thanks_acknowledgement.md": ("assistant", "thanks_acknowledgement.md"),
    "faq/holiday_greetings.md": ("assistant", "holiday_greetings.md"),
    "faq/short_replies_examples.md": ("assistant", "short_replies_examples.md"),
    "faq/short_replies_rules_and_principles.md": ("assistant", "short_replies_rules_and_principles.md"),
    "faq/no_answer_matrix.md": ("assistant", "no_answer_matrix.md"),
    "faq/no_contact_staff_silence.md": ("assistant", "no_contact_staff_silence.md"),
    "faq/admin_scheduling_routing.md": ("assistant", "admin_scheduling_routing.md"),
    "faq/staff_escalation.md": ("assistant", "staff_escalation.md"),
    "faq/video_attachment_ack.md": ("assistant", "video_attachment_ack.md"),
    "faq/demo_highlights.md": ("assistant", "demo_highlights.md"),
    # --- marketing ---
    "marketing/promotions.md": ("marketing", "promotions.md"),
    "marketing/success_stories.md": ("marketing", "success_stories.md"),
    "faq/why_choose_little_scholars.md": ("marketing", "why_choose.md"),
    # --- complaint ---
    "complaint/response.md": ("complaint", "response.md"),
}

FM_KEYS = ("folder", "type")


def _patch_frontmatter(text: str, folder: str, typ: str) -> str:
    if not text.startswith("---"):
        header = f"---\nlanguage: en\ntype: {typ}\nfolder: {folder}\n---\n\n"
        return header + text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return text
    fm = parts[1]
    body = parts[2]
    lines = fm.splitlines()
    seen = set()
    out = []
    for line in lines:
        if not line.strip():
            out.append(line)
            continue
        m = re.match(r"^(\w+)\s*:", line)
        if not m:
            out.append(line)
            continue
        key = m.group(1)
        if key == "folder":
            out.append(f"folder: {folder}")
            seen.add("folder")
        elif key == "type":
            out.append(f"type: {typ}")
            seen.add("type")
        else:
            out.append(line)
    if "folder" not in seen:
        out.append(f"folder: {folder}")
    if "type" not in seen:
        out.append(f"type: {typ}")
    return "---" + "\n".join(out) + "\n---" + body


def _sidecar_paths(md: Path) -> list[Path]:
    """Return associated metadata sidecars if present."""
    out = []
    for p in (
        Path(str(md) + ".metadata.json"),
        md.with_suffix(".metadata.json"),  # rare alt naming
    ):
        if p.exists() and p not in out:
            out.append(p)
    return out


def migrate_lang(lang: str, *, dry_run: bool = False) -> Counter:
    base = CONTENT / lang
    counts: Counter = Counter()
    missing: list[str] = []
    for src_rel, (folder, dest_rel) in MOVE.items():
        src = base / Path(src_rel)
        if not src.exists():
            # Some langs may lag EN; skip quietly but record
            missing.append(f"{lang}/{src_rel}")
            continue
        dest = base / folder / dest_rel
        typ = FOLDER_TYPE[folder]
        counts[folder] += 1
        if dry_run:
            print(f"  {lang}/{src_rel} -> {lang}/{folder}/{dest_rel}")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        text = src.read_text(encoding="utf-8")
        text = _patch_frontmatter(text, folder, typ)
        dest.write_text(text, encoding="utf-8")
        # move sidecars
        for side in _sidecar_paths(src):
            # Prefer dest.md.metadata.json naming
            if side.name.endswith(".md.metadata.json"):
                side_dest = Path(str(dest) + ".metadata.json")
            else:
                side_dest = dest.with_suffix(".metadata.json")
            side_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(side), str(side_dest))
        if src.resolve() != dest.resolve():
            src.unlink()
    if missing and lang == "en":
        raise SystemExit(f"EN missing expected sources:\n" + "\n".join(missing))
    elif missing:
        print(f"  [{lang}] skipped missing ({len(missing)}):")
        for m in missing[:12]:
            print(f"    - {m}")
        if len(missing) > 12:
            print(f"    ... +{len(missing) - 12} more")
    return counts


def cleanup_empty_dirs(lang: str) -> None:
    base = CONTENT / lang
    # deepest first
    for d in sorted(base.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if d.is_dir():
            try:
                next(d.iterdir())
            except StopIteration:
                d.rmdir()


def orphan_check(lang: str) -> list[str]:
    base = CONTENT / lang
    known_dest = {f"{folder}/{dest}" for folder, dest in MOVE.values()}
    known_src = set(MOVE.keys())
    orphans = []
    for p in base.rglob("*.md"):
        rel = str(p.relative_to(base)).replace("\\", "/")
        if rel in known_dest:
            continue
        # leftover under old folders
        top = rel.split("/")[0]
        if top in {
            "faq",
            "courses",
            "institution",
            "guides",
            "policies",
            "marketing",
            "complaint",
        } or rel not in known_dest:
            # After move, only new folders should remain; flag anything not mapped dest
            if rel not in known_dest:
                orphans.append(rel)
    return orphans


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    assert len(MOVE) == 106, f"expected 106 mappings, got {len(MOVE)}"

    print(f"Migrating {len(MOVE)} docs × {len(LANGS)} langs")
    for lang in LANGS:
        print(f"\n== {lang} ==")
        counts = migrate_lang(lang, dry_run=args.dry_run)
        print("  counts:", dict(sorted(counts.items())))
        if not args.dry_run:
            cleanup_empty_dirs(lang)
            orphans = orphan_check(lang)
            # Ignore junk non-md already handled; flag leftover md
            leftover = [
                o
                for o in orphans
                if o.split("/")[0]
                in {"faq", "courses", "institution", "guides"}
                or (
                    o.split("/")[0] == "policies"
                    and o
                    not in {
                        f"policies/{d}"
                        for f, d in MOVE.values()
                        if f == "policies"
                    }
                )
            ]
            # Better: any md not under expected new folders as dest
            expected_tops = set(FOLDER_TYPE)
            leftover = [
                o
                for o in orphans
                if o.split("/")[0] not in expected_tops
            ]
            if leftover:
                print(f"  LEFTOVER ({len(leftover)}):")
                for o in leftover:
                    print(f"    - {o}")
            else:
                print("  leftover old folders: none")


if __name__ == "__main__":
    main()
