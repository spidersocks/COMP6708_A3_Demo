import React, { useEffect, useMemo, useRef, useState } from "react";
import WaMarkdown from "./WaMarkdown.jsx";
import "./brightnest.css";

// Prefer same-origin `/brightnest-api` (Vercel rewrite → SAE) so HTTPS pages
// are not blocked by mixed content when talking to the HTTP EIP.
const API_BASE = (
  import.meta.env.VITE_BRIGHTNEST_API_URL ||
  import.meta.env.VITE_API_BASE_URL ||
  "/brightnest-api"
).replace(/\/$/, "");

const LOGO_SRC = "/projects/brightnest/logo.png";

const DEMO_PROBES = [
  { label: "Hours", text: "What are your opening hours?" },
  { label: "Tomorrow", text: "tomorrow?" },
  { label: "Christmas", text: "Are you open on Christmas Day?" },
  { label: "Phonics fee", text: "How much is phonics?" },
  { label: "Teachers", text: "Tell me about your teachers" },
  { label: "Location", text: "Where are you located?" },
  { label: "Trial slots", text: "Show trial class availability" },
  { label: "Phonics seats", text: "Show phonics availability" },
  { label: "Math seats", text: "Show early math availability" },
  { label: "Avery slots", text: "Show Avery open slots for trial" },
  { label: "Jordan slots", text: "Show Jordan open slots for phonics" },
  { label: "Book Jason", text: "Can I book a class for Jason next week?" },
  { label: "Cancel class", text: "Can I cancel Jason's class next Tuesday?" },
  { label: "Unknown ask", text: "Does BrightNest offer scuba diving for toddlers?" },
  { label: "Private 1:1", text: "How much is private English tuition?" },
  { label: "Talk to staff", text: "Can I speak to a real person please?" },
  { label: "Angry parent", text: "This is unacceptable — I'm furious about today's class!" },
];

function getOrCreateSessionId() {
  let id = localStorage.getItem("bn_bot_session_id");
  if (!id) {
    id = "web:" + Math.random().toString(36).slice(2, 10) + Date.now();
    localStorage.setItem("bn_bot_session_id", id);
  }
  return id;
}

function formatTime(d = new Date()) {
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function shortSessionLabel() {
  const id = localStorage.getItem("bn_bot_session_id");
  if (!id) return "web:new";
  return id.length > 20 ? `${id.slice(0, 18)}…` : id;
}

function scoreLabel(score) {
  if (score == null || Number.isNaN(Number(score))) return null;
  return Number(score).toFixed(3);
}

function routeMeta(route) {
  if (route === "deterministic_opening_hours") {
    return { label: "Opening-hours engine", tone: "hours" };
  }
  if (route === "booking_dialogue") {
    return { label: "Booking dialogue", tone: "book" };
  }
  if (route === "cancel_dialogue") {
    return { label: "Cancel dialogue", tone: "book" };
  }
  if (route === "booking_tool") {
    return { label: "Booking tool", tone: "book" };
  }
  if (route === "rag") {
    return { label: "RAG retrieve → generate", tone: "rag" };
  }
  if (route === "staff_escalation") {
    return { label: "Staff escalation → boss inbox", tone: "book" };
  }
  if (route === "unknown_programme") {
    return { label: "Unknown programme (quiet admin flag)", tone: "rag" };
  }
  return { label: "Waiting for a query", tone: "idle" };
}

function Reveal({ title, summary, defaultOpen = false, children }) {
  return (
    <details className="bn-reveal" defaultOpen={defaultOpen}>
      <summary>
        <span className="bn-reveal-title">{title}</span>
        {summary ? <span className="bn-reveal-summary">{summary}</span> : null}
      </summary>
      <div className="bn-reveal-body">{children}</div>
    </details>
  );
}

export default function BrightNestBotDemo() {
  const [inputMessage, setInputMessage] = useState("");
  const [chatHistory, setChatHistory] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);
  const [courses, setCourses] = useState([]);
  const [courseKey, setCourseKey] = useState("trial");
  const [parentName, setParentName] = useState("Demo Parent");
  const [childName, setChildName] = useState("Jason Chan");
  const [phone, setPhone] = useState("+852 9000 0000");
  const [lastBooking, setLastBooking] = useState(null);
  const [pendingSlot, setPendingSlot] = useState(null);
  const [sessionLabel, setSessionLabel] = useState(() => shortSessionLabel());
  const [trace, setTrace] = useState(null);
  const [apiOnline, setApiOnline] = useState(null);
  const [schedule, setSchedule] = useState(null);
  const [scheduleTeacherId, setScheduleTeacherId] = useState("all");
  const [adminTodos, setAdminTodos] = useState([]);
  const [staffMessages, setStaffMessages] = useState([]);

  const chatWindowRef = useRef(null);
  const textAreaRef = useRef(null);

  useEffect(() => {
    const setVh = () => {
      document.documentElement.style.setProperty("--vh", `${window.innerHeight * 0.01}px`);
    };
    setVh();
    window.addEventListener("resize", setVh);
    return () => window.removeEventListener("resize", setVh);
  }, []);

  useEffect(() => {
    fetch(`${API_BASE}/healthz`)
      .then((r) => setApiOnline(r.ok))
      .catch(() => setApiOnline(false));
    fetch(`${API_BASE}/courses`)
      .then((r) => r.json())
      .then((d) => setCourses(d.courses || []))
      .catch(() => {});
    fetch(`${API_BASE}/schedule?days=7`)
      .then((r) => r.json())
      .then((d) => setSchedule(d))
      .catch(() => setSchedule(null));
    refreshAdminTodos();
    refreshStaffMessages();
  }, []);

  function refreshAdminTodos() {
    fetch(`${API_BASE}/admin/todos?status=open`)
      .then((r) => r.json())
      .then((d) => setAdminTodos(d.todos || []))
      .catch(() => setAdminTodos([]));
  }

  function refreshStaffMessages() {
    fetch(`${API_BASE}/admin/messages?status=open`)
      .then((r) => r.json())
      .then((d) => setStaffMessages(d.messages || []))
      .catch(() => setStaffMessages([]));
  }

  async function markTodoDone(id) {
    try {
      await fetch(`${API_BASE}/admin/todos/${encodeURIComponent(id)}/done`, {
        method: "POST",
      });
      refreshAdminTodos();
    } catch {
      /* ignore */
    }
  }

  async function ackStaffMessage(id) {
    try {
      await fetch(`${API_BASE}/admin/messages/${encodeURIComponent(id)}/ack`, {
        method: "POST",
      });
      refreshStaffMessages();
    } catch {
      /* ignore */
    }
  }

  useEffect(() => {
    if (chatWindowRef.current) {
      chatWindowRef.current.scrollTop = chatWindowRef.current.scrollHeight;
    }
  }, [chatHistory, isLoading]);

  useEffect(() => {
    const el = textAreaRef.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 88)}px`;
  }, [inputMessage]);

  function handleReset() {
    const sid = localStorage.getItem("bn_bot_session_id");
    const finish = () => {
      setChatHistory([]);
      setError(null);
      setLastBooking(null);
      setPendingSlot(null);
      setTrace(null);
      setAdminTodos([]);
      setStaffMessages([]);
      localStorage.removeItem("bn_bot_session_id");
      setSessionLabel(shortSessionLabel());
      refreshAdminTodos();
      refreshStaffMessages();
    };
    if (sid) {
      fetch(`${API_BASE}/chat/history/${encodeURIComponent(sid)}`, {
        method: "DELETE",
      })
        .catch(() => {})
        .finally(finish);
    } else {
      // Still clear admin to-dos for a clean demo even without a session id
      fetch(`${API_BASE}/chat/history/${encodeURIComponent("demo-reset")}`, {
        method: "DELETE",
      })
        .catch(() => {})
        .finally(finish);
    }
  }

  async function sendMessage(raw) {
    const trimmed = (raw ?? inputMessage).trim();
    if (!trimmed || isLoading) return;

    const now = formatTime();
    setChatHistory((prev) => [...prev, { role: "user", message: trimmed, time: now }]);
    setIsLoading(true);
    setError(null);
    setInputMessage("");
    setSessionLabel(shortSessionLabel());

    try {
      const res = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: trimmed,
          session_id: getOrCreateSessionId(),
          debug: true,
          phone,
          parent_name: parentName,
          child_name: childName,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setSessionLabel(shortSessionLabel());
      setTrace(data.trace || null);
      if (data.trace?.admin_todo) refreshAdminTodos();
      if (data.trace?.staff_message) refreshStaffMessages();
      // Keep form identity in sync when dialogue resolves a child
      const resolvedChild =
        data.trace?.booking?.child_name || data.trace?.cancel?.child_name;
      if (resolvedChild) setChildName(resolvedChild);
      const resolvedParent = data.trace?.booking?.parent_name;
      if (resolvedParent) setParentName(resolvedParent);
      setChatHistory((prev) => [
        ...prev,
        {
          role: "bot",
          message: data.answer || "(no answer)",
          time: formatTime(),
          citations: data.citations || [],
          slots: data.slots || null,
          provider: data.provider,
        },
      ]);
    } catch (err) {
      console.error(err);
      setError("Backend unreachable — check SAE / CORS / mixed content.");
      setApiOnline(false);
      setChatHistory((prev) =>
        prev.length && prev[prev.length - 1]?.role === "user" ? prev.slice(0, -1) : prev
      );
    } finally {
      setIsLoading(false);
    }
  }

  async function bookSlot(slot) {
    setPendingSlot(slot);
    setCourseKey(slot.course_key || courseKey);
    try {
      const res = await fetch(`${API_BASE}/bookings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          course_key: slot.course_key || courseKey,
          slot_start: slot.slot_start,
          parent_name: parentName || "Demo Parent",
          child_name: childName || "Demo Child",
          phone: phone || "+852 9000 0000",
          notes: "Booked from COMP6708 technical demo UI",
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Booking failed");
      setLastBooking(data);
      setChatHistory((prev) => [
        ...prev,
        {
          role: "bot",
          message:
            `Booking confirmed!\n` +
            `• Ref: ${data.id}\n` +
            `• ${data.course_label}\n` +
            `• ${data.slot_display}\n` +
            `• ${data.teacher_name || "Teacher TBC"}` +
            (data.room ? ` · ${data.room}` : ""),
          time: formatTime(),
          booking: data,
        },
      ]);
      // Refresh admin register so seats_left updates in the inspector
      fetch(`${API_BASE}/schedule?days=7`)
        .then((r) => r.json())
        .then((d) => setSchedule(d))
        .catch(() => {});
    } catch (err) {
      setError(err.message || "Booking failed");
    } finally {
      setPendingSlot(null);
    }
  }

  const isDisabled = isLoading || !inputMessage.trim();
  const showEmpty = chatHistory.length === 0 && !isLoading;
  const route = trace?.route;
  const meta = routeMeta(route);
  const steps = trace?.pipeline_steps || [];
  const facts = trace?.opening_hours?.facts;
  const rag = trace?.rag;
  const hits = rag?.hits || [...chatHistory].reverse().find((m) => m.role === "bot")?.citations || [];

  const turnKey = String(trace?.latency_ms ?? "idle");
  const hoursOpen = route === "deterministic_opening_hours";
  const ragOpen = route === "rag";
  const bookingOpen =
    route === "booking_tool" || route === "booking_dialogue" || route === "cancel_dialogue";
  const dialogue = trace?.booking;
  const cancelTrace = trace?.cancel;
  const teachers = schedule?.teachers || [];
  const scheduleDays = useMemo(() => {
    const days = schedule?.days || [];
    if (scheduleTeacherId === "all") return days;
    return days.map((day) => ({
      ...day,
      sessions: (day.sessions || []).filter((s) => s.teacher_id === scheduleTeacherId),
    }));
  }, [schedule, scheduleTeacherId]);
  const scheduleSummary = schedule
    ? `${teachers.length} teachers · ${scheduleDays.reduce((n, d) => n + (d.sessions?.length || 0), 0)} sessions / 7d`
    : "Synthetic admin register";

  const hoursSummary = useMemo(() => {
    if (!facts) return "Idle until a date/hours query";
    if (facts.weather_hint) return "Severe weather closure";
    if (facts.is_holiday) return facts.holiday_name || "Public holiday";
    if (facts.is_sunday) return "Sunday · closed";
    if (facts.open_time) return `${facts.open_time}–${facts.close_time}`;
    return "Parsed";
  }, [facts]);

  const ragSummary = useMemo(() => {
    if (!rag && !hits.length) return "Idle until a KB question";
    const n = rag?.hit_count ?? hits.length;
    return `${n} chunk${n === 1 ? "" : "s"} · ${rag?.embedding_mode || "embed"}`;
  }, [rag, hits.length]);

  return (
    <div className="bn-app">
      <header className="bn-top">
        <div className="bn-brand-block">
          <img src={LOGO_SRC} alt="" className="bn-mark" />
          <div>
            <p className="bn-eyebrow">COMP6708 A3 · technical demo</p>
            <h1>
              BrightNest <span>Learning Centre</span>
            </h1>
          </div>
        </div>
        <div className="bn-top-actions">
          <span className={`bn-dot ${apiOnline ? "ok" : apiOnline === false ? "bad" : ""}`}>
            {apiOnline ? "API online" : apiOnline === false ? "API offline" : "API…"}
          </span>
          <code className="bn-sid">{sessionLabel}</code>
          <button type="button" className="bn-ghost" onClick={handleReset} disabled={isLoading}>
            Reset
          </button>
        </div>
      </header>

      <div className="bn-stage">
        {/* Phone */}
        <aside className="bn-phone-wrap">
          <p className="bn-col-label">Parent chat</p>
          <div className="bn-phone">
            <div className="bn-phone-bar">
              <img src={LOGO_SRC} alt="BrightNest" />
              <div>
                <strong>Parent chat</strong>
                <small>messaging surface</small>
              </div>
            </div>

            <div className="bn-messages" ref={chatWindowRef}>
              {showEmpty && (
                <div className="bn-row bn-row-bot">
                  <div className="bn-bubble bn-bubble-bot">
                    <WaMarkdown>Hi! Ask about courses, fees, hours, or trial booking.</WaMarkdown>
                    <span className="bn-time">{formatTime()}</span>
                  </div>
                </div>
              )}

              {chatHistory.map((msg, idx) => (
                <div
                  key={idx}
                  className={`bn-row ${msg.role === "user" ? "bn-row-user" : "bn-row-bot"}`}
                >
                  <div
                    className={`bn-bubble ${
                      msg.role === "user" ? "bn-bubble-user" : "bn-bubble-bot"
                    }`}
                  >
                    {msg.role === "bot" ? (
                      <WaMarkdown>{msg.message}</WaMarkdown>
                    ) : (
                      <p className="bn-msg-text">{msg.message}</p>
                    )}

                    {msg.slots?.length > 0 && (
                      <div className="bn-slots">
                        {msg.slots.slice(0, 4).map((slot) => (
                          <div className="bn-slot" key={slot.slot_start}>
                            <div>
                              <strong>{slot.slot_display}</strong>
                              <span>
                                {slot.course_label}
                                {slot.teacher_name ? ` · ${slot.teacher_name}` : ""}
                                {slot.room ? ` · ${slot.room}` : ""}
                                {` · ${slot.seats_left} left`}
                              </span>
                            </div>
                            <button
                              type="button"
                              disabled={!!pendingSlot}
                              onClick={() => bookSlot(slot)}
                            >
                              Book
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                    <span className="bn-time">{msg.time || formatTime()}</span>
                  </div>
                </div>
              ))}

              {isLoading && (
                <div className="bn-row bn-row-bot">
                  <div className="bn-bubble bn-bubble-bot">
                    <div className="bn-typing">
                      <span className="dot" />
                      <span className="dot" />
                      <span className="dot" />
                    </div>
                  </div>
                </div>
              )}
              {error && <div className="bn-error">{error}</div>}
            </div>

            <form
              className="bn-inputbar"
              onSubmit={(e) => {
                e.preventDefault();
                sendMessage();
              }}
            >
              <textarea
                ref={textAreaRef}
                className="bn-textarea"
                value={inputMessage}
                onChange={(e) => setInputMessage(e.target.value)}
                placeholder="Message as a parent…"
                disabled={isLoading}
                rows={1}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey && !isDisabled) {
                    e.preventDefault();
                    sendMessage();
                  }
                }}
              />
              <button className="bn-send" type="submit" disabled={isDisabled} aria-label="Send">
                Send
              </button>
            </form>
          </div>
        </aside>

        {/* Inspector */}
        <section className="bn-inspector">
          <p className="bn-col-label">System inspector</p>

          <div className="bn-route-card">
            <div className="bn-route-main">
              <span className={`bn-pill bn-pill-${meta.tone}`}>{meta.label}</span>
              {trace?.latency_ms != null && <span className="bn-latency">{trace.latency_ms} ms</span>}
              {isLoading && <span className="bn-running">running…</span>}
            </div>
            {steps.length > 0 && (
              <ol className="bn-step-rail" aria-label="Pipeline steps">
                {steps.map((s) => (
                  <li key={s.id} className={`bn-step-chip bn-step-${s.status || "idle"}`} title={s.detail}>
                    <span>{s.label}</span>
                  </li>
                ))}
              </ol>
            )}
            <p className="bn-hint">
              Only the active path opens below. Expand others when you want the detail.
            </p>
          </div>

          <Reveal
            key={`hours-${turnKey}`}
            title="Date · holiday · weather"
            summary={hoursSummary}
            defaultOpen={hoursOpen}
          >
            {!facts ? (
              <p className="bn-empty">Ask an hours/date question to populate this panel.</p>
            ) : (
              <ul className="bn-fact-list">
                <li>
                  <span>Resolved</span>
                  <code>{facts.datetime}</code>
                </li>
                <li>
                  <span>Matched via</span>
                  <code>
                    {facts.parse_debug?.matched_via ||
                      (facts.parse_debug?.is_fallback_to_now ? "fallback_now" : "—")}
                  </code>
                </li>
                <li>
                  <span>Window</span>
                  <code>
                    {facts.open_time && facts.close_time
                      ? `${facts.open_time}–${facts.close_time}`
                      : facts.is_sunday
                        ? "closed (Sunday)"
                        : "—"}
                  </code>
                </li>
                <li>
                  <span>Holiday</span>
                  <code>{facts.is_holiday ? facts.holiday_name || "yes" : "no"}</code>
                </li>
                <li>
                  <span>HKO weather</span>
                  <code>{facts.weather_hint || "none / not today"}</code>
                </li>
                {trace?.opening_hours?.effective_message && (
                  <li>
                    <span>Effective msg</span>
                    <code>{trace.opening_hours.effective_message}</code>
                  </li>
                )}
              </ul>
            )}
          </Reveal>

          <Reveal key={`rag-${turnKey}`} title="RAG retrieval" summary={ragSummary} defaultOpen={ragOpen}>
            {hits.length === 0 ? (
              <p className="bn-empty">
                {route === "rag"
                  ? "No chunks returned."
                  : "Skipped when hours/booking short-circuits."}
              </p>
            ) : (
              <>
                <div className="bn-rag-strip">
                  <span>index {rag?.index_size ?? "—"}</span>
                  <span>top_k {rag?.top_k ?? "—"}</span>
                  {rag?.language_fallback ? <span>lang fallback</span> : null}
                </div>
                <ul className="bn-hits">
                  {hits.slice(0, 5).map((h, i) => (
                    <li key={`${h.source}-${i}`}>
                      <div className="bn-hit-head">
                        <strong>{h.title || h.canonical || h.source || `Hit ${i + 1}`}</strong>
                        {scoreLabel(h.score) && <code>{scoreLabel(h.score)}</code>}
                      </div>
                      <span className="bn-hit-src">{h.source}</span>
                      {h.snippet ? <p>{h.snippet}</p> : null}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </Reveal>

          <Reveal
            key={`register-${turnKey}`}
            title="Class register"
            summary={scheduleSummary}
            defaultOpen={bookingOpen || !trace}
          >
            <p className="bn-note">
              Backed by local CSV admin sheets (teachers, windows, sessions, enrolments,
              trial_slots). Chat bookings append to <code>chat_bookings.csv</code> — same
              idea as writing into a live spreadsheet, without Google OAuth.
              {schedule?.sheets_dir ? (
                <>
                  {" "}
                  <code className="bn-path">{schedule.sheets_dir}</code>
                </>
              ) : null}
            </p>
            <div className="bn-register-filters">
              <label>
                Teacher
                <select
                  value={scheduleTeacherId}
                  onChange={(e) => setScheduleTeacherId(e.target.value)}
                >
                  <option value="all">All teachers</option>
                  {teachers.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {!schedule ? (
              <p className="bn-empty">Schedule API offline — restart local backend to load.</p>
            ) : (
              <div className="bn-register">
                {teachers.length > 0 && scheduleTeacherId === "all" && (
                  <ul className="bn-teacher-windows">
                    {teachers.map((t) => (
                      <li key={t.id}>
                        <strong>{t.name}</strong>
                        <span>
                          {t.role} · {t.courses.join(", ")}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
                {scheduleDays.map((day) =>
                  day.sessions?.length ? (
                    <div className="bn-reg-day" key={day.date}>
                      <h4>{day.label}</h4>
                      <ul>
                        {day.sessions.map((s) => (
                          <li key={`${s.session_id}-${s.slot_start}`}>
                            <div className="bn-reg-line">
                              <strong>
                                {s.slot_display.split(", ").pop()} · {s.course_label}
                              </strong>
                              <code className={s.status === "open" ? "ok" : ""}>
                                {s.seats_left}/{s.capacity}
                              </code>
                            </div>
                            <span>
                              {s.teacher_name}
                              {s.room ? ` · ${s.room}` : ""}
                              {s.kind === "trial" ? " · trial" : ""}
                              {s.enrolled_count
                                ? ` · ${s.enrolled_count} on register`
                                : ""}
                              {s.chat_bookings ? ` · +${s.chat_bookings} chat` : ""}
                            </span>
                            {s.enrolled?.length > 0 && (
                              <em>
                                {s.enrolled
                                  .slice(0, 4)
                                  .map((e) => e.child)
                                  .join(", ")}
                                {s.enrolled.length > 4
                                  ? ` +${s.enrolled.length - 4}`
                                  : ""}
                              </em>
                            )}
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null
                )}
              </div>
            )}
          </Reveal>

          <Reveal
            key={`admin-${turnKey}`}
            title="Admin to-do"
            summary={
              adminTodos.length
                ? `${adminTodos.length} open · quiet KB / programme gaps`
                : "Empty — quiet flags when RAG can't answer"
            }
            defaultOpen={Boolean(trace?.admin_todo) || adminTodos.length > 0}
          >
            <p className="bn-note">
              Internal research queue (<code>admin_todos.csv</code>). Parents are{" "}
              <strong>never</strong> told about this sheet — they only hear a helpful
              reply (and can ask to speak with our team).
            </p>
            {adminTodos.length === 0 ? (
              <p className="bn-empty">No open to-dos.</p>
            ) : (
              <ul className="bn-todos">
                {adminTodos.map((t) => (
                  <li key={t.id}>
                    <div className="bn-todo-head">
                      <strong>{t.parent_name || t.phone || "Unknown parent"}</strong>
                      <code>{t.id}</code>
                    </div>
                    <span className="bn-todo-q">Q: {t.question}</span>
                    <span className="bn-todo-a">A: {(t.reply || "").slice(0, 160)}</span>
                    {t.info_needed ? (
                      <span className="bn-todo-need">Need: {t.info_needed}</span>
                    ) : null}
                    <button type="button" className="bn-ghost" onClick={() => markTodoDone(t.id)}>
                      Mark done
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Reveal>

          <Reveal
            key={`boss-${turnKey}`}
            title="Admin messages"
            summary={
              staffMessages.length
                ? `${staffMessages.length} open · bot → centre boss`
                : "Empty — human hand-off notices land here"
            }
            defaultOpen={Boolean(trace?.staff_message) || staffMessages.length > 0}
          >
            <p className="bn-note">
              Direct notices to the centre lead (<code>staff_messages.csv</code>) when a
              parent asks for a real person, is very upset, raises safety concerns, or
              needs a human decision. Parent hears:{" "}
              <em>“I've let our team know… somebody will reach out during working hours.”</em>
            </p>
            {staffMessages.length === 0 ? (
              <p className="bn-empty">No open staff messages.</p>
            ) : (
              <ul className="bn-todos">
                {staffMessages.map((m) => (
                  <li key={m.id}>
                    <div className="bn-todo-head">
                      <strong>
                        {(m.priority || "normal").toUpperCase()} · {m.category || "general"}
                      </strong>
                      <code>{m.id}</code>
                    </div>
                    <span className="bn-todo-q">
                      From: {m.parent_name || m.phone || "Unknown"}
                      {m.child_name ? ` · ${m.child_name}` : ""}
                    </span>
                    <span className="bn-todo-need">Boss: {m.summary_for_boss}</span>
                    <span className="bn-todo-a">Parent said: {(m.parent_message || "").slice(0, 200)}</span>
                    <button
                      type="button"
                      className="bn-ghost"
                      onClick={() => ackStaffMessage(m.id)}
                    >
                      Acknowledge
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Reveal>

          <Reveal
            key={`book-${turnKey}`}
            title="Booking tool"
            summary={
              cancelTrace?.stage
                ? `cancel · ${cancelTrace.stage}${
                    cancelTrace.child_name ? ` · ${cancelTrace.child_name}` : ""
                  }`
                : dialogue?.stage
                  ? `${dialogue.stage}${dialogue.child_name ? ` · ${dialogue.child_name}` : ""}${
                      dialogue.course_key ? ` · ${dialogue.course_key}` : ""
                    }`
                  : trace?.booking?.course_key
                    ? `${trace.booking.course_key} · ${trace.booking.slot_count ?? 0} slots`
                    : "Idle — try book / cancel for Jason"
            }
            defaultOpen={bookingOpen}
          >
            <p className="bn-note">
              Identity comes from the messaging number (<code>families.csv</code>). Demo
              number <code>+852 9000 0000</code> → Demo Parent / Jason Chan + Mia Chan.
            </p>
            {dialogue?.child_schedule?.length > 0 && (
              <ul className="bn-child-sched">
                {dialogue.child_schedule.slice(0, 4).map((s) => (
                  <li key={s.slot_start}>
                    <strong>{s.slot_display}</strong>
                    <span>
                      {s.course_label} · {s.teacher_name}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <div className="bn-book-grid">
              <label>
                Course
                <select value={courseKey} onChange={(e) => setCourseKey(e.target.value)}>
                  {(courses.length
                    ? courses
                    : [
                        { key: "trial", label: "Trial Assessment" },
                        { key: "phonics", label: "Phonics" },
                      ]
                  ).map((c) => (
                    <option key={c.key} value={c.key}>
                      {c.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Parent
                <input value={parentName} onChange={(e) => setParentName(e.target.value)} />
              </label>
              <label>
                Child
                <input value={childName} onChange={(e) => setChildName(e.target.value)} />
              </label>
              <label>
                Phone
                <input value={phone} onChange={(e) => setPhone(e.target.value)} />
              </label>
            </div>
            <div className="bn-book-actions">
              <button
                type="button"
                className="bn-secondary"
                disabled={isLoading}
                onClick={() => sendMessage(`Can I book a class for ${childName.split(" ")[0]} next week?`)}
              >
                Start booking dialogue
              </button>
              <button
                type="button"
                className="bn-secondary"
                disabled={isLoading}
                onClick={() =>
                  sendMessage(`Can I cancel ${childName.split(" ")[0]}'s class next Tuesday?`)
                }
              >
                Start cancel dialogue
              </button>
              <button
                type="button"
                className="bn-secondary"
                disabled={isLoading}
                onClick={() => sendMessage(`Show availability for ${courseKey}`)}
              >
                Browse slots only
              </button>
            </div>
            {lastBooking && (
              <p className="bn-note">
                Last booking <code>{lastBooking.id}</code> · {lastBooking.slot_display}
                {lastBooking.teacher_name ? ` · ${lastBooking.teacher_name}` : ""}
              </p>
            )}
          </Reveal>

          <Reveal
            key={`mem-${turnKey}`}
            title="Session memory"
            summary={`${trace?.history?.turns_loaded ?? 0}/${trace?.history?.limit ?? 10} msgs`}
            defaultOpen={false}
          >
            {trace?.history?.preview ? (
              <pre className="bn-pre">{trace.history.preview}</pre>
            ) : (
              <p className="bn-empty">No prior turns yet.</p>
            )}
          </Reveal>

          <Reveal
            key={`probes-${turnKey}`}
            title="Demo probes"
            summary="Quick paths for the live talk"
            defaultOpen={!trace}
          >
            <div className="bn-probes">
              {DEMO_PROBES.map((p) => (
                <button
                  key={p.text}
                  type="button"
                  className="bn-probe"
                  disabled={isLoading}
                  onClick={() => sendMessage(p.text)}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </Reveal>
        </section>
      </div>
    </div>
  );
}
