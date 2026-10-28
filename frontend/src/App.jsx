import React, { useEffect, useRef, useState } from "react";

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

const SUGGESTIONS = [
  "What are your opening hours?",
  "How much is phonics?",
  "Show trial class availability",
  "Where are you located?",
  "Tell me about your teachers",
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

export default function App() {
  const [inputMessage, setInputMessage] = useState("");
  const [chatHistory, setChatHistory] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);
  const [courses, setCourses] = useState([]);
  const [courseKey, setCourseKey] = useState("trial");
  const [parentName, setParentName] = useState("");
  const [childName, setChildName] = useState("");
  const [phone, setPhone] = useState("");
  const [lastBooking, setLastBooking] = useState(null);
  const [pendingSlot, setPendingSlot] = useState(null);

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
    fetch(`${API_BASE}/courses`)
      .then((r) => r.json())
      .then((d) => setCourses(d.courses || []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (chatWindowRef.current) {
      chatWindowRef.current.scrollTop = chatWindowRef.current.scrollHeight;
    }
  }, [chatHistory, isLoading]);

  useEffect(() => {
    const el = textAreaRef.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 140)}px`;
  }, [inputMessage]);

  function handleReset() {
    setChatHistory([]);
    setError(null);
    setLastBooking(null);
    setPendingSlot(null);
    localStorage.removeItem("bn_bot_session_id");
  }

  async function sendMessage(raw) {
    const trimmed = (raw ?? inputMessage).trim();
    if (!trimmed || isLoading) return;

    const now = formatTime();
    setChatHistory((prev) => [...prev, { role: "user", message: trimmed, time: now }]);
    setIsLoading(true);
    setError(null);
    setInputMessage("");

    try {
      const res = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: trimmed,
          session_id: getOrCreateSessionId(),
          debug: true,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
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
      setError("Could not reach the BrightNest backend. Is it running on port 8000?");
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
          notes: "Booked from COMP6708 demo UI",
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
            `We'll hold this seat for the demo — no payment required.`,
          time: formatTime(),
          booking: data,
        },
      ]);
    } catch (err) {
      setError(err.message || "Booking failed");
    } finally {
      setPendingSlot(null);
    }
  }

  async function loadAvailability() {
    await sendMessage(`Show availability for ${courseKey}`);
  }

  const isDisabled = isLoading || !inputMessage.trim();

  return (
    <div className="bn-app">
      <header className="bn-hero">
        <h1 className="bn-brand">
          BrightNest <span>Learning Centre</span>
        </h1>
        <p className="bn-sub">
          亮巢學習中心 — bilingual kids&apos; learning centre assistant with self-built RAG,
          live availability, and trial booking (COMP6708 demo).
        </p>
      </header>

      <div className="bn-shell">
        <section className="bn-chat-card">
          <div className="bn-chat-header">
            <div className="bn-chat-header-left">
              <div className="bn-avatar">
                <img
                  src="/logo.png"
                  alt="BrightNest"
                  onError={(e) => {
                    e.currentTarget.style.display = "none";
                    e.currentTarget.parentElement.textContent = "BN";
                  }}
                />
              </div>
              <div>
                <p className="bn-chat-title">BrightNest Assistant</p>
                <p className="bn-chat-meta">
                  Session: {localStorage.getItem("bn_bot_session_id") || "(new)"}
                </p>
              </div>
            </div>
            <button className="bn-reset" onClick={handleReset} disabled={isLoading}>
              Reset
            </button>
          </div>

          <div className="bn-messages" ref={chatWindowRef}>
            {chatHistory.length === 0 && !isLoading && (
              <div className="bn-row bn-row-bot">
                <div className="bn-bubble bn-bubble-bot">
                  Hello! I&apos;m the BrightNest assistant. Ask about courses, fees, hours — or
                  check trial availability and book a seat.
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
                  <div>{msg.message}</div>

                  {msg.slots?.length > 0 && (
                    <div className="bn-slots">
                      {msg.slots.slice(0, 5).map((slot) => (
                        <div className="bn-slot" key={slot.slot_start}>
                          <div>
                            <strong>{slot.slot_display}</strong>
                            <span>
                              {slot.course_label} · {slot.seats_left} seat(s) left
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

                  {msg.citations?.length > 0 && (
                    <details className="bn-citations">
                      <summary>Sources used ({msg.citations.length})</summary>
                      <ul className="bn-cite-list">
                        {msg.citations.map((c, i) => (
                          <li key={i}>
                            {c.title || c.source || "doc"}
                            {c.score != null ? ` · score ${Number(c.score).toFixed(2)}` : ""}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}

                  <span className="bn-time">
                    {msg.time || formatTime()}
                    {msg.provider ? ` · ${msg.provider}` : ""}
                  </span>
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
              placeholder="Ask about courses, fees, hours, or booking…"
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
              <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor">
                <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2 .01 7z" />
              </svg>
            </button>
          </form>
        </section>

        <aside className="bn-side">
          <div className="bn-side-card">
            <h2>Try these</h2>
            <p>Quick prompts for the 5-minute live demo path.</p>
            <div className="bn-chips">
              {SUGGESTIONS.map((s) => (
                <button key={s} type="button" className="bn-chip" onClick={() => sendMessage(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>

          <div className="bn-side-card">
            <h2>Quick book</h2>
            <p>Pick a course, load slots in chat, then tap Book on a card.</p>
            <div className="bn-form">
              <label htmlFor="course">Course</label>
              <select
                id="course"
                value={courseKey}
                onChange={(e) => setCourseKey(e.target.value)}
              >
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
              <label htmlFor="parent">Parent name</label>
              <input
                id="parent"
                value={parentName}
                onChange={(e) => setParentName(e.target.value)}
                placeholder="Demo Parent"
              />
              <label htmlFor="child">Child name</label>
              <input
                id="child"
                value={childName}
                onChange={(e) => setChildName(e.target.value)}
                placeholder="Demo Child"
              />
              <label htmlFor="phone">Phone</label>
              <input
                id="phone"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="+852 9000 0000"
              />
              <button type="button" onClick={loadAvailability}>
                Show availability
              </button>
            </div>
            {lastBooking && (
              <div className="bn-booking-ok" style={{ marginTop: 12 }}>
                Last booking <strong>{lastBooking.id}</strong> — {lastBooking.slot_display}
              </div>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
