import React, { useMemo, useState } from "react";

const MYCALENDAR_ICON = "/projects/brightnest/mycalendar.webp";
const DAY_START_H = 9;
const DAY_END_H = 18;
const HOUR_PX = 44;

function parseSlot(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d;
}

function eventTop(iso) {
  const d = parseSlot(iso);
  if (!d) return 0;
  const mins = d.getHours() * 60 + d.getMinutes() - DAY_START_H * 60;
  return Math.max(0, (mins / 60) * HOUR_PX);
}

function eventHeight(durationMin) {
  const h = ((Number(durationMin) || 60) / 60) * HOUR_PX;
  return Math.max(22, h - 2);
}

function courseTone(courseKey) {
  const k = (courseKey || "").toLowerCase();
  if (k.includes("phonic")) return "phonic";
  if (k.includes("mandarin") || k.includes("chinese")) return "mandarin";
  if (k.includes("writ")) return "writing";
  if (k.includes("math")) return "math";
  if (k.includes("play")) return "playgroup";
  if (k.includes("trial")) return "trial";
  return "default";
}

/**
 * Google-Calendar-style week board for the BrightNest class register.
 */
export default function MyCalendar({
  schedule,
  teacherId = "all",
  studentName = "all",
  onTeacherChange,
  onStudentChange,
  summaryNote,
}) {
  const [weekIndex, setWeekIndex] = useState(0);
  const teachers = schedule?.teachers || [];
  const allDays = schedule?.days || [];

  const students = useMemo(() => {
    const names = new Set();
    for (const day of allDays) {
      for (const s of day.sessions || []) {
        for (const e of s.enrolled || []) {
          if (e?.child) names.add(e.child);
        }
      }
    }
    return Array.from(names).sort((a, b) => a.localeCompare(b));
  }, [allDays]);

  const weekDays = useMemo(() => {
    const start = weekIndex * 7;
    return allDays.slice(start, start + 7);
  }, [allDays, weekIndex]);

  const maxWeek = Math.max(0, Math.ceil(allDays.length / 7) - 1);

  const filteredDays = useMemo(() => {
    return weekDays.map((day) => ({
      ...day,
      sessions: (day.sessions || []).filter((s) => {
        if (teacherId !== "all" && s.teacher_id !== teacherId) return false;
        if (studentName !== "all") {
          const kids = (s.enrolled || []).map((e) => e.child);
          if (!kids.includes(studentName)) return false;
        }
        return true;
      }),
    }));
  }, [weekDays, teacherId, studentName]);

  const hours = [];
  for (let h = DAY_START_H; h < DAY_END_H; h += 1) {
    hours.push(h);
  }
  const gridH = (DAY_END_H - DAY_START_H) * HOUR_PX;

  const rangeLabel =
    filteredDays.length > 0
      ? `${filteredDays[0].label} – ${filteredDays[filteredDays.length - 1].label}`
      : "No days loaded";

  return (
    <div className="bn-cal">
      <div className="bn-cal-chrome">
        <div className="bn-cal-brand">
          <img src={MYCALENDAR_ICON} alt="" className="bn-cal-logo" />
          <div>
            <strong>MyCalendar</strong>
            <small>BrightNest class register</small>
          </div>
        </div>
        <div className="bn-cal-nav">
          <button
            type="button"
            className="bn-cal-nav-btn"
            disabled={weekIndex <= 0}
            onClick={() => setWeekIndex((w) => Math.max(0, w - 1))}
            aria-label="Previous week"
          >
            ‹
          </button>
          <span className="bn-cal-range">{rangeLabel}</span>
          <button
            type="button"
            className="bn-cal-nav-btn"
            disabled={weekIndex >= maxWeek}
            onClick={() => setWeekIndex((w) => Math.min(maxWeek, w + 1))}
            aria-label="Next week"
          >
            ›
          </button>
          <button
            type="button"
            className="bn-cal-today"
            onClick={() => setWeekIndex(0)}
          >
            Today
          </button>
        </div>
      </div>

      <div className="bn-cal-filters">
        <label>
          Staff
          <select
            value={teacherId}
            onChange={(e) => onTeacherChange?.(e.target.value)}
          >
            <option value="all">All staff</option>
            {teachers.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Student
          <select
            value={studentName}
            onChange={(e) => onStudentChange?.(e.target.value)}
          >
            <option value="all">All students</option>
            {students.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
      </div>

      {summaryNote ? <p className="bn-note bn-cal-note">{summaryNote}</p> : null}

      {!schedule ? (
        <p className="bn-empty">Schedule API offline — restart backend to load.</p>
      ) : filteredDays.length === 0 ? (
        <p className="bn-empty">No sessions in this window.</p>
      ) : (
        <div className="bn-cal-board">
          <div
            className="bn-cal-head"
            style={{ gridTemplateColumns: `48px repeat(${filteredDays.length}, minmax(0, 1fr))` }}
          >
            <div className="bn-cal-gutter-head" />
            {filteredDays.map((day) => (
              <div key={day.date} className="bn-cal-day-head">
                <span>{day.label?.split(" ")[0]}</span>
                <strong>{day.label?.replace(/^\w+\s*/, "") || day.date}</strong>
              </div>
            ))}
          </div>
          <div
            className="bn-cal-body"
            style={{ gridTemplateColumns: `48px repeat(${filteredDays.length}, minmax(0, 1fr))` }}
          >
            <div className="bn-cal-gutter" style={{ height: gridH }}>
              {hours.map((h) => (
                <div key={h} className="bn-cal-hour" style={{ height: HOUR_PX }}>
                  {String(h).padStart(2, "0")}:00
                </div>
              ))}
            </div>
            {filteredDays.map((day) => (
              <div key={day.date} className="bn-cal-col" style={{ height: gridH }}>
                {hours.map((h) => (
                  <div
                    key={`${day.date}-${h}`}
                    className="bn-cal-slot-line"
                    style={{ top: (h - DAY_START_H) * HOUR_PX }}
                  />
                ))}
                {(day.sessions || []).map((s) => {
                  const kids = (s.enrolled || [])
                    .map((e) => e.child)
                    .filter(Boolean)
                    .slice(0, 3);
                  const extra = Math.max(0, (s.enrolled || []).length - kids.length);
                  return (
                    <div
                      key={`${s.session_id}-${s.slot_start}`}
                      className={`bn-cal-event tone-${courseTone(s.course_key)} ${
                        s.status === "full" ? "is-full" : ""
                      }`}
                      style={{
                        top: eventTop(s.slot_start),
                        height: eventHeight(s.duration_min),
                      }}
                      title={`${s.course_label} · ${s.teacher_name} · ${s.seats_left}/${s.capacity} seats`}
                    >
                      <strong>{s.course_label}</strong>
                      <span>
                        {s.teacher_name}
                        {s.room ? ` · ${s.room}` : ""}
                      </span>
                      <span className="bn-cal-seats">
                        {s.seats_left}/{s.capacity}
                        {s.kind === "trial" ? " · trial" : ""}
                      </span>
                      {kids.length > 0 ? (
                        <em>
                          {kids.join(", ")}
                          {extra ? ` +${extra}` : ""}
                        </em>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
