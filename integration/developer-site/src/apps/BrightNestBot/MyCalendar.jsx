import React, { useEffect, useMemo, useState } from "react";

const MYCALENDAR_ICON = "/projects/brightnest/mycalendar.webp";
const DAY_START_H = 9;
const DAY_END_H = 18;
const HOUR_PX = 48;

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
  return Math.max(20, h - 3);
}

function eventEndMs(iso, durationMin) {
  const d = parseSlot(iso);
  if (!d) return 0;
  return d.getTime() + (Number(durationMin) || 60) * 60000;
}

function formatClock(iso) {
  const d = parseSlot(iso);
  if (!d) return "—";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

function endClock(iso, durationMin) {
  const d = parseSlot(iso);
  if (!d) return "—";
  const end = new Date(d.getTime() + (Number(durationMin) || 60) * 60000);
  return end.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
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

function dayParts(label, dateIso) {
  const d = dateIso ? new Date(`${dateIso}T12:00:00`) : null;
  if (d && !Number.isNaN(d.getTime())) {
    return {
      weekday: d.toLocaleDateString(undefined, { weekday: "short" }),
      dayNum: d.getDate(),
      isToday: new Date().toDateString() === d.toDateString(),
    };
  }
  const bits = String(label || "").split(/\s+/);
  return { weekday: bits[0] || "", dayNum: bits[1] || "", isToday: false };
}

/**
 * Pack overlapping sessions into side-by-side columns (Google Calendar style).
 * Returns sessions with { _col, _cols, _startMs, _endMs }.
 */
function layoutOverlaps(sessions) {
  const items = (sessions || [])
    .map((s) => {
      const start = parseSlot(s.slot_start);
      if (!start) return null;
      return {
        ...s,
        _startMs: start.getTime(),
        _endMs: eventEndMs(s.slot_start, s.duration_min),
      };
    })
    .filter(Boolean)
    .sort((a, b) => a._startMs - b._startMs || a._endMs - b._endMs);

  // Cluster into overlap groups, then assign columns within each group.
  const groups = [];
  let cur = [];
  let curEnd = 0;
  for (const item of items) {
    if (!cur.length || item._startMs < curEnd) {
      cur.push(item);
      curEnd = Math.max(curEnd, item._endMs);
    } else {
      groups.push(cur);
      cur = [item];
      curEnd = item._endMs;
    }
  }
  if (cur.length) groups.push(cur);

  const laid = [];
  for (const group of groups) {
    const colEnds = [];
    for (const item of group) {
      let col = colEnds.findIndex((end) => end <= item._startMs);
      if (col === -1) {
        col = colEnds.length;
        colEnds.push(item._endMs);
      } else {
        colEnds[col] = item._endMs;
      }
      item._col = col;
    }
    const cols = Math.max(1, colEnds.length);
    for (const item of group) {
      item._cols = cols;
      laid.push(item);
    }
  }
  return laid;
}

/**
 * Staff-desk Google Calendar–style week board for the BrightNest register.
 */
export default function MyCalendar({
  schedule,
  teacherId = "all",
  studentName = "all",
  onTeacherChange,
  onStudentChange,
}) {
  const [weekIndex, setWeekIndex] = useState(0);
  const [selected, setSelected] = useState(null);
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
      sessions: layoutOverlaps(
        (day.sessions || []).filter((s) => {
          if (teacherId !== "all" && s.teacher_id !== teacherId) return false;
          if (studentName !== "all") {
            const kids = (s.enrolled || []).map((e) => e.child);
            if (!kids.includes(studentName)) return false;
          }
          return true;
        })
      ),
    }));
  }, [weekDays, teacherId, studentName]);

  useEffect(() => {
    setSelected(null);
  }, [weekIndex, teacherId, studentName]);

  const hours = [];
  for (let h = DAY_START_H; h < DAY_END_H; h += 1) hours.push(h);
  const gridH = (DAY_END_H - DAY_START_H) * HOUR_PX;

  const rangeLabel =
    filteredDays.length > 0
      ? `${filteredDays[0].label} – ${filteredDays[filteredDays.length - 1].label}`
      : "No days loaded";

  const monthTitle = (() => {
    const first = filteredDays[0]?.date;
    if (!first) return "MyCalendar";
    const d = new Date(`${first}T12:00:00`);
    if (Number.isNaN(d.getTime())) return "MyCalendar";
    return d.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  })();

  return (
    <div className="bn-cal bn-cal--staff">
      <div className="bn-cal-chrome">
        <div className="bn-cal-brand">
          <img src={MYCALENDAR_ICON} alt="" className="bn-cal-logo" />
          <div>
            <strong>MyCalendar</strong>
            <small>{monthTitle}</small>
          </div>
        </div>
        <div className="bn-cal-nav">
          <button
            type="button"
            className="bn-cal-today"
            onClick={() => setWeekIndex(0)}
          >
            Today
          </button>
          <button
            type="button"
            className="bn-cal-nav-btn"
            disabled={weekIndex <= 0}
            onClick={() => setWeekIndex((w) => Math.max(0, w - 1))}
            aria-label="Previous week"
          >
            ‹
          </button>
          <button
            type="button"
            className="bn-cal-nav-btn"
            disabled={weekIndex >= maxWeek}
            onClick={() => setWeekIndex((w) => Math.min(maxWeek, w + 1))}
            aria-label="Next week"
          >
            ›
          </button>
          <span className="bn-cal-range">{rangeLabel}</span>
        </div>
        <div className="bn-cal-filters">
          <select
            value={teacherId}
            onChange={(e) => onTeacherChange?.(e.target.value)}
            aria-label="Filter by staff"
          >
            <option value="all">All staff</option>
            {teachers.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
          <select
            value={studentName}
            onChange={(e) => onStudentChange?.(e.target.value)}
            aria-label="Filter by student"
          >
            <option value="all">All students</option>
            {students.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </div>
      </div>

      {!schedule ? (
        <p className="bn-empty bn-cal-empty">Schedule API offline — restart backend to load.</p>
      ) : filteredDays.length === 0 ? (
        <p className="bn-empty bn-cal-empty">No sessions in this window.</p>
      ) : (
        <div className={`bn-cal-main ${selected ? "has-detail" : ""}`}>
          <div className="bn-cal-board">
            <div
              className="bn-cal-head"
              style={{
                gridTemplateColumns: `56px repeat(${filteredDays.length}, minmax(0, 1fr))`,
              }}
            >
              <div className="bn-cal-gutter-head" />
              {filteredDays.map((day) => {
                const parts = dayParts(day.label, day.date);
                return (
                  <div
                    key={day.date}
                    className={`bn-cal-day-head ${parts.isToday ? "is-today" : ""}`}
                  >
                    <span>{parts.weekday}</span>
                    <strong>{parts.dayNum}</strong>
                  </div>
                );
              })}
            </div>
            <div
              className="bn-cal-body"
              style={{
                gridTemplateColumns: `56px repeat(${filteredDays.length}, minmax(0, 1fr))`,
              }}
            >
              <div className="bn-cal-gutter" style={{ height: gridH }}>
                {hours.map((h) => (
                  <div key={h} className="bn-cal-hour" style={{ height: HOUR_PX }}>
                    {h === DAY_START_H ? "" : `${String(h).padStart(2, "0")}:00`}
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
                    const selKey = `${s.session_id}|${s.slot_start}`;
                    const active =
                      selected &&
                      selected.session_id === s.session_id &&
                      selected.slot_start === s.slot_start;
                    const cols = Math.max(1, s._cols || 1);
                    const col = s._col || 0;
                    const widthPct = 100 / cols;
                    const leftPct = col * widthPct;
                    return (
                      <button
                        key={selKey}
                        type="button"
                        className={`bn-cal-event tone-${courseTone(s.course_key)} ${
                          s.status === "full" ? "is-full" : ""
                        } ${active ? "is-selected" : ""} ${cols > 1 ? "is-split" : ""}`}
                        style={{
                          top: eventTop(s.slot_start),
                          height: eventHeight(s.duration_min),
                          left: `calc(${leftPct}% + 1px)`,
                          width: `calc(${widthPct}% - 2px)`,
                          right: "auto",
                        }}
                        onClick={() =>
                          setSelected({
                            ...s,
                            dayLabel: day.label,
                            dayDate: day.date,
                          })
                        }
                      >
                        <strong>{s.course_label}</strong>
                        <span>
                          {formatClock(s.slot_start)}
                          {s.kind === "trial" ? " · trial" : ""}
                        </span>
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>
          </div>

          {selected ? (
            <aside className="bn-cal-detail open">
              <div className="bn-cal-detail-head">
                <span className={`bn-cal-dot tone-${courseTone(selected.course_key)}`} />
                <div>
                  <h3>{selected.course_label}</h3>
                  <p>
                    {selected.dayLabel} · {formatClock(selected.slot_start)}–
                    {endClock(selected.slot_start, selected.duration_min)}
                  </p>
                </div>
                <button
                  type="button"
                  className="bn-cal-detail-close"
                  onClick={() => setSelected(null)}
                  aria-label="Close details"
                >
                  ×
                </button>
              </div>
              <dl className="bn-cal-detail-grid">
                <div>
                  <dt>Staff</dt>
                  <dd>{selected.teacher_name || "—"}</dd>
                </div>
                <div>
                  <dt>Room</dt>
                  <dd>{selected.room || "—"}</dd>
                </div>
                <div>
                  <dt>Seats</dt>
                  <dd>
                    {selected.seats_left}/{selected.capacity} left
                    {selected.status === "full" ? " · full" : ""}
                  </dd>
                </div>
                <div>
                  <dt>Type</dt>
                  <dd>{selected.kind === "trial" ? "Trial assessment" : "Regular class"}</dd>
                </div>
              </dl>
              <h4>Enrolled ({selected.enrolled_count || 0})</h4>
              {(selected.enrolled || []).length === 0 ? (
                <p className="bn-empty">No students on the register for this slot.</p>
              ) : (
                <ul className="bn-cal-roster">
                  {(selected.enrolled || []).map((e, i) => (
                    <li key={`${e.child}-${i}`}>
                      <strong>{e.child}</strong>
                      <span>{e.parent || "—"}</span>
                    </li>
                  ))}
                </ul>
              )}
              {selected.chat_bookings ? (
                <p className="bn-cal-chat-note">
                  +{selected.chat_bookings} chat booking
                  {selected.chat_bookings === 1 ? "" : "s"} counted in seats
                </p>
              ) : null}
            </aside>
          ) : null}
        </div>
      )}
    </div>
  );
}
