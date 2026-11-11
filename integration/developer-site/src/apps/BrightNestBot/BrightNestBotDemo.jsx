import React, { useEffect, useMemo, useRef, useState } from "react";
import WaMarkdown from "./WaMarkdown.jsx";
import MyCalendar from "./MyCalendar.jsx";
import TurnPipeline from "./TurnPipeline.jsx";
import "./brightnest.css";

// Prefer same-origin `/brightnest-api` (Vercel rewrite → SAE) so HTTPS pages
// are not blocked by mixed content when talking to the HTTP EIP.
const API_BASE = (
  import.meta.env.VITE_BRIGHTNEST_API_URL ||
  import.meta.env.VITE_API_BASE_URL ||
  "/brightnest-api"
).replace(/\/$/, "");

const LOGO_SRC = "/projects/brightnest/logo.png";
const SHEETPRO_ICON = "/projects/brightnest/sheetpro.webp";
const MAILBOX_ICON = "/projects/brightnest/mailbox.webp";
const FAMILIES_ICON = "/projects/brightnest/logo.png";
const MYCALENDAR_ICON = "/projects/brightnest/mycalendar.webp";
/** How many calendar days MyCalendar loads (API max 21). */
const SCHEDULE_DAYS = 14;

/** Demo WhatsApp identities — Jason is on families.csv; New Parent is blank. */
const PARENT_PROFILES = [
  {
    id: "jason",
    label: "Demo Parent",
    short: "Jason Chan",
    blurb: "Known family · bookings on file",
    parentName: "Demo Parent",
    childName: "Jason Chan",
    phone: "+852 9000 0000",
    initials: "JC",
  },
  {
    id: "new",
    label: "New parent",
    short: "No child yet",
    blurb: "Blank WhatsApp · nothing on file",
    parentName: "",
    childName: "",
    phone: "+852 9000 9999",
    initials: "NP",
  },
];

/** Survives site reload; cleared on Reset so the gate returns only then. */
const PARENT_PICK_KEY = "bn_parent_picked";

function readPickedProfileId() {
  try {
    const v = localStorage.getItem(PARENT_PICK_KEY);
    return v === "jason" || v === "new" ? v : null;
  } catch {
    return null;
  }
}

function profileById(id) {
  return PARENT_PROFILES.find((p) => p.id === id) || PARENT_PROFILES[0];
}

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

const REASON_LABELS = {
  knowledge_gap: "Missing centre information",
  unknown_programme: "Asked about a programme we don't offer",
  no_retrieval: "No matching centre materials found",
  weak_retrieval: "Only a weak match in centre materials",
  off_topic_retrieval: "Centre materials didn't match the question",
  weak_extractive: "Auto-answer looked incomplete — please confirm",
  model_no_answer: "Assistant couldn't answer from centre materials",
  model_admin_gap: "Needs a staff decision or private record",
  generic_fallback: "Only a generic reply was available",
};

const CATEGORY_LABELS = {
  safety: "Child safety / medical concern",
  angry: "Upset parent / complaint",
  hard_conflict: "Refund or withdrawal request",
  ask_human: "Asked to speak with staff",
  general: "General follow-up",
};

const PRIORITY_LABELS = {
  urgent: "Urgent",
  high: "High",
  normal: "Normal",
};

function reasonLabel(reason) {
  const raw = (reason || "").trim();
  if (!raw) return "Needs staff follow-up";
  return REASON_LABELS[raw] || raw;
}

function categoryLabel(category, fallback) {
  if (fallback) return fallback;
  const raw = (category || "").trim();
  if (!raw) return CATEGORY_LABELS.general;
  return CATEGORY_LABELS[raw] || raw.replace(/_/g, " ");
}

function priorityLabel(priority, fallback) {
  if (fallback) return fallback;
  const raw = (priority || "normal").trim().toLowerCase();
  return PRIORITY_LABELS[raw] || raw;
}

function clip(text, n = 120) {
  const s = (text || "").trim();
  if (s.length <= n) return s;
  return `${s.slice(0, n - 1)}…`;
}

const LEGACY_MAIL_SUBJECTS = new Set([
  "Parent asked to speak with a real staff member / manager.",
  "Parent sounds very upset or is escalating a complaint — boss/staff should follow up.",
  "Possible child safety / medical urgency — staff must contact the parent ASAP.",
  "Refund / withdrawal / public-complaint pressure — needs a human decision.",
]);

function mailSubject(m) {
  const raw = (m?.summary_for_boss || "").trim();
  if (raw && !LEGACY_MAIL_SUBJECTS.has(raw)) return raw;
  return categoryLabel(m?.category, m?.category_label);
}

function mailSnippet(m) {
  const parent = (m?.parent_message || "").trim();
  if (parent) return clip(parent, 72);
  const body = (m?.email_body || "").trim();
  if (!body) return "";
  const line =
    body
      .split(/\n+/)
      .map((s) => s.trim())
      .find((s) => s && !/^hi team/i.test(s) && !/^thanks/i.test(s)) || body;
  return clip(line.replace(/^["“]|["”]$/g, ""), 72);
}

function mailEmailBody(m) {
  const drafted = (m?.email_body || "").trim();
  if (drafted) return drafted;
  const parent = m?.parent_name || m?.phone || "Parent (name not on file)";
  const phone = m?.phone || "not provided";
  const child = m?.child_name || "—";
  const quote = (m?.parent_message || "").trim() || "(no message captured)";
  const told = (m?.bot_reply || "").trim() || "(no automated reply recorded)";
  return [
    "Hi team,",
    "",
    "The BrightNest chatbot handed this parent over to the centre desk.",
    "",
    `Subject: ${mailSubject(m)}`,
    `Priority: ${priorityLabel(m?.priority, m?.priority_label)}`,
    `Topic: ${categoryLabel(m?.category, m?.category_label)}`,
    "",
    `Parent: ${parent}`,
    `Phone / WhatsApp: ${phone}`,
    `Child: ${child}`,
    "",
    "What the parent wrote:",
    `“${quote}”`,
    "",
    "What we already told them in chat:",
    `“${told}”`,
    "",
    "Please follow up during working hours.",
    "",
    "Thanks,",
    "BrightNest chatbot",
  ].join("\n");
}

export default function BrightNestBotDemo() {
  const [inputMessage, setInputMessage] = useState("");
  const [chatHistory, setChatHistory] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);
  const [parentGateOpen, setParentGateOpen] = useState(() => !readPickedProfileId());
  const [profileId, setProfileId] = useState(() => readPickedProfileId() || "jason");
  const [parentName, setParentName] = useState(
    () => profileById(readPickedProfileId() || "jason").parentName
  );
  const [childName, setChildName] = useState(
    () => profileById(readPickedProfileId() || "jason").childName
  );
  const [phone, setPhone] = useState(
    () => profileById(readPickedProfileId() || "jason").phone
  );
  const [sessionLabel, setSessionLabel] = useState(() => shortSessionLabel());
  const [trace, setTrace] = useState(null);
  const [apiOnline, setApiOnline] = useState(null);
  const [schedule, setSchedule] = useState(null);
  const [scheduleTeacherId, setScheduleTeacherId] = useState("all");
  const [scheduleStudent, setScheduleStudent] = useState("all");
  const [adminTodos, setAdminTodos] = useState([]);
  const [staffMessages, setStaffMessages] = useState([]);
  const [familiesRoster, setFamiliesRoster] = useState([]);
  /** Phones already acknowledged — badge only for *new* families since last view/reset. */
  const [seenFamilyPhones, setSeenFamilyPhones] = useState(() => new Set());
  const [rightView, setRightView] = useState("system"); // system | staff
  const [staffTab, setStaffTab] = useState("calendar"); // calendar | sheet | mail | families
  const [selectedMsgId, setSelectedMsgId] = useState(null);
  const [selectedFamilyPhone, setSelectedFamilyPhone] = useState(null);
  const [readMailIds, setReadMailIds] = useState(() => new Set());
  const [sheetSel, setSheetSel] = useState({
    addr: "A1",
    text: "Follow-up sheet",
  });

  const chatWindowRef = useRef(null);
  const textAreaRef = useRef(null);
  const familiesBaselineRef = useRef(false);

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
    refreshSchedule();
    refreshAdminTodos();
    refreshStaffMessages();
    refreshFamilies({ markSeen: true });
  }, []);

  function refreshSchedule() {
    fetch(`${API_BASE}/schedule?days=${SCHEDULE_DAYS}`)
      .then((r) => r.json())
      .then((d) => setSchedule(d))
      .catch(() => setSchedule(null));
  }

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

  function refreshFamilies({ markSeen = false } = {}) {
    fetch(`${API_BASE}/admin/families?days_ahead=21`)
      .then((r) => r.json())
      .then((d) => {
        const list = d.families || [];
        setFamiliesRoster(list);
        if (markSeen || !familiesBaselineRef.current) {
          familiesBaselineRef.current = true;
          setSeenFamilyPhones(new Set(list.map((f) => f.phone).filter(Boolean)));
        }
      })
      .catch(() => {
        setFamiliesRoster([]);
        if (markSeen || !familiesBaselineRef.current) {
          familiesBaselineRef.current = true;
          setSeenFamilyPhones(new Set());
        }
      });
  }

  function switchParentProfile(nextId) {
    const profile = PARENT_PROFILES.find((p) => p.id === nextId) || PARENT_PROFILES[0];
    try {
      localStorage.setItem(PARENT_PICK_KEY, profile.id);
    } catch {
      /* ignore */
    }
    setProfileId(profile.id);
    setParentName(profile.parentName);
    setChildName(profile.childName);
    setPhone(profile.phone);
    setParentGateOpen(false);
    // Keep chat + session until System Reset — profile only changes who we speak as.
    setError(null);
    refreshFamilies();
    refreshSchedule();
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

  async function patchTodoField(id, fields) {
    try {
      const res = await fetch(`${API_BASE}/admin/todos/${encodeURIComponent(id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(fields),
      });
      if (!res.ok) return;
      const updated = await res.json();
      setAdminTodos((prev) => prev.map((t) => (t.id === id ? { ...t, ...updated } : t)));
    } catch {
      /* ignore */
    }
  }

  async function ackStaffMessage(id) {
    try {
      await fetch(`${API_BASE}/admin/messages/${encodeURIComponent(id)}/ack`, {
        method: "POST",
      });
      setSelectedMsgId((cur) => (cur === id ? null : cur));
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
      setTrace(null);
      setAdminTodos([]);
      setStaffMessages([]);
      setSelectedMsgId(null);
      setReadMailIds(new Set());
      setRightView("system");
      setStaffTab("calendar");
      setSelectedFamilyPhone(null);
      familiesBaselineRef.current = false;
      setSeenFamilyPhones(new Set());
      localStorage.removeItem("bn_bot_session_id");
      try {
        localStorage.removeItem(PARENT_PICK_KEY);
      } catch {
        /* ignore */
      }
      setParentGateOpen(true);
      setSessionLabel(shortSessionLabel());
      refreshAdminTodos();
      refreshStaffMessages();
      refreshFamilies({ markSeen: true });
      refreshSchedule();
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
    if (parentGateOpen) return;
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
      const nextTrace = data.trace ? { ...data.trace } : {};
      if (!nextTrace.staff_message && data.debug?.staff_message) {
        nextTrace.staff_message = data.debug.staff_message;
      }
      if (!nextTrace.admin_todo && data.debug?.admin_todo) {
        nextTrace.admin_todo = data.debug.admin_todo;
      }
      setTrace(Object.keys(nextTrace).length ? nextTrace : null);
      const hitTodo = Boolean(nextTrace.admin_todo);
      const hitBoss =
        Boolean(nextTrace.staff_message) || nextTrace.route === "staff_escalation";
      if (hitTodo) {
        refreshAdminTodos();
        setStaffTab("sheet");
        setRightView("staff");
      }
      if (hitBoss) {
        refreshStaffMessages();
        if (nextTrace.staff_message?.id) {
          setSelectedMsgId(nextTrace.staff_message.id);
        }
        // Last write wins when both fire in one turn
        setStaffTab("mail");
        setRightView("staff");
      }
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
          provider: data.provider,
        },
      ]);
      // Dialogue bookings mutate chat_bookings.csv — keep MyCalendar + Families in sync
      if (
        data.booking ||
        nextTrace.route === "booking_dialogue" ||
        nextTrace.route === "booking_tool" ||
        nextTrace.route === "cancel_dialogue"
      ) {
        refreshSchedule();
        refreshFamilies();
        if (!hitTodo && !hitBoss) {
          setStaffTab("calendar");
          setRightView("staff");
        }
      }
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

  const isDisabled = parentGateOpen || isLoading || !inputMessage.trim();
  const showEmpty = chatHistory.length === 0 && !isLoading;
  const turnKey = String(trace?.latency_ms ?? "idle");

  const selectedMsg = useMemo(() => {
    if (!staffMessages.length) return null;
    return staffMessages.find((m) => m.id === selectedMsgId) || staffMessages[0];
  }, [staffMessages, selectedMsgId]);

  useEffect(() => {
    if (staffTab !== "mail" || !selectedMsg?.id) return;
    setReadMailIds((prev) => {
      if (prev.has(selectedMsg.id)) return prev;
      const next = new Set(prev);
      next.add(selectedMsg.id);
      return next;
    });
  }, [staffTab, selectedMsg?.id]);

  const unreadMailCount = useMemo(
    () => staffMessages.reduce((n, m) => n + (readMailIds.has(m.id) ? 0 : 1), 0),
    [staffMessages, readMailIds]
  );

  const newFamiliesCount = useMemo(
    () =>
      familiesBaselineRef.current
        ? familiesRoster.reduce(
            (n, f) => n + (f.phone && !seenFamilyPhones.has(f.phone) ? 1 : 0),
            0
          )
        : 0,
    [familiesRoster, seenFamilyPhones]
  );

  const sheetCols = ["A", "B", "C", "D", "E", "F", "G"];

  function selectSheetCell(addr, text) {
    setSheetSel({
      addr,
      text: text == null || text === "" ? "—" : String(text),
    });
  }

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
          <div className={`bn-phone ${parentGateOpen ? "bn-phone--gated" : ""}`}>
            <div className="bn-phone-bar">
              <img src={LOGO_SRC} alt="BrightNest" />
              <div>
                <strong>Parent chat</strong>
                <small>messaging surface</small>
              </div>
            </div>

            {parentGateOpen ? (
              <div className="bn-parent-gate" role="dialog" aria-label="Choose parent identity">
                <p className="bn-parent-gate-kicker">Start a WhatsApp thread</p>
                <h2>Who are you messaging as?</h2>
                <p className="bn-parent-gate-copy">
                  Pick a known demo family or a blank new parent. Chat stays locked until you choose.
                  Reloading the page keeps your last choice; Reset brings this picker back.
                </p>
                <div className="bn-parent-gate-actions">
                  {PARENT_PROFILES.map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      className="bn-parent-gate-card"
                      onClick={() => switchParentProfile(p.id)}
                    >
                      <span className="bn-profile-avatar" aria-hidden="true">
                        {p.initials}
                      </span>
                      <span className="bn-profile-copy">
                        <strong>{p.label}</strong>
                        <small>{p.blurb}</small>
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="bn-profile-switch" role="group" aria-label="Demo parent profile">
                {PARENT_PROFILES.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    className={`bn-profile-card ${profileId === p.id ? "active" : ""}`}
                    onClick={() => switchParentProfile(p.id)}
                    disabled={isLoading}
                  >
                    <span className="bn-profile-avatar" aria-hidden="true">
                      {p.initials}
                    </span>
                    <span className="bn-profile-copy">
                      <strong>{p.label}</strong>
                      <small>{p.blurb}</small>
                    </span>
                  </button>
                ))}
                <div className="bn-profile-active">
                  <span>{childName || "No child on file"}</span>
                  <code>{phone}</code>
                </div>
              </div>
            )}

            <div
              className="bn-messages"
              ref={chatWindowRef}
              aria-hidden={parentGateOpen}
            >
              {showEmpty && !parentGateOpen && (
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
                placeholder={
                  parentGateOpen ? "Choose a parent above to unlock chat…" : "Message as a parent…"
                }
                disabled={parentGateOpen || isLoading}
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

        {/* Inspector / Staff desk — label row aligns with Parent chat; body with phone */}
        <div className="bn-right-wrap">
          <div className="bn-right-head">
            <p className="bn-col-label">
              {rightView === "staff" ? "Staff desk" : "System inspector"}
            </p>
            <div className="bn-view-toggle" role="tablist" aria-label="Right panel">
              <button
                type="button"
                role="tab"
                aria-selected={rightView === "system"}
                className={rightView === "system" ? "active" : ""}
                onClick={() => setRightView("system")}
              >
                System
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={rightView === "staff"}
                className={rightView === "staff" ? "active" : ""}
                onClick={() => setRightView("staff")}
              >
                Staff desk
                {(adminTodos.length > 0 || staffMessages.length > 0) && (
                  <span className="bn-view-badge">
                    {adminTodos.length + staffMessages.length}
                  </span>
                )}
              </button>
            </div>
          </div>

          <section
            className={`bn-inspector${
              rightView === "staff" ? " bn-inspector--staff" : " bn-inspector--system"
            }`}
          >
          {/* Keep both panes mounted so the flowchart does not remount/refit on tab switch. */}
          <div
            className={`bn-pane-system${rightView === "system" ? " is-visible" : ""}`}
            aria-hidden={rightView !== "system"}
          >
            <TurnPipeline
              trace={trace}
              answer={
                [...chatHistory].reverse().find((m) => m.role === "bot")?.message || ""
              }
              parentMessage={
                [...chatHistory].reverse().find((m) => m.role === "user")?.message || ""
              }
              turnKey={turnKey}
              chatHistory={chatHistory}
            />
          </div>
          <div
            className={`bn-pane-staff${rightView === "staff" ? " is-visible" : ""}`}
            aria-hidden={rightView !== "staff"}
          >
            <div className="bn-browser">
              <div className="bn-browser-chrome">
                <div className="bn-browser-dots" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                </div>
                <div className="bn-browser-tabs" role="tablist" aria-label="Staff apps">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={staffTab === "calendar"}
                    className={`bn-browser-tab ${staffTab === "calendar" ? "active" : ""}`}
                    onClick={() => {
                      setStaffTab("calendar");
                      refreshSchedule();
                    }}
                  >
                    <img
                      src={MYCALENDAR_ICON}
                      alt=""
                      className="bn-tab-ico bn-tab-ico-img"
                      aria-hidden="true"
                    />
                    <span className="bn-browser-tab-label">MyCalendar</span>
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={staffTab === "sheet"}
                    className={`bn-browser-tab ${staffTab === "sheet" ? "active" : ""}`}
                    onClick={() => setStaffTab("sheet")}
                  >
                    <img
                      src={SHEETPRO_ICON}
                      alt=""
                      className="bn-tab-ico bn-tab-ico-img"
                      aria-hidden="true"
                    />
                    <span className="bn-browser-tab-label">SheetPro</span>
                    {adminTodos.length > 0 && (
                      <span className="bn-tab-count">{adminTodos.length}</span>
                    )}
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={staffTab === "mail"}
                    className={`bn-browser-tab ${staffTab === "mail" ? "active" : ""}`}
                    onClick={() => setStaffTab("mail")}
                  >
                    <img
                      src={MAILBOX_ICON}
                      alt=""
                      className="bn-tab-ico bn-tab-ico-img"
                      aria-hidden="true"
                    />
                    <span className="bn-browser-tab-label">MailBox</span>
                    {unreadMailCount > 0 && (
                      <span className="bn-tab-count">{unreadMailCount}</span>
                    )}
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={staffTab === "families"}
                    className={`bn-browser-tab ${staffTab === "families" ? "active" : ""}`}
                    onClick={() => {
                      setStaffTab("families");
                      refreshFamilies({ markSeen: true });
                    }}
                  >
                    <img
                      src={FAMILIES_ICON}
                      alt=""
                      className="bn-tab-ico bn-tab-ico-img"
                      aria-hidden="true"
                    />
                    <span className="bn-browser-tab-label">Families</span>
                    {newFamiliesCount > 0 && (
                      <span className="bn-tab-count">{newFamiliesCount}</span>
                    )}
                  </button>
                </div>
              </div>
              <div className="bn-browser-url">
                <span className="bn-browser-lock" aria-hidden="true" />
                <code>
                  {staffTab === "calendar"
                    ? "calendar.google.com/calendar/u/0/r/week"
                    : staffTab === "sheet"
                      ? "sheetpro.app/d/brightnest-followups"
                      : staffTab === "mail"
                        ? "mailbox.app/u/0/#inbox"
                        : "families.app/d/brightnest-roster"}
                </code>
              </div>

              <div className="bn-browser-page">
                {staffTab === "calendar" ? (
                  <MyCalendar
                    schedule={schedule}
                    teacherId={scheduleTeacherId}
                    studentName={scheduleStudent}
                    onTeacherChange={setScheduleTeacherId}
                    onStudentChange={setScheduleStudent}
                  />
                ) : staffTab === "sheet" ? (
                  <div className="bn-sheet-widget">
                    <div className="bn-sheet-titlebar">
                      <div className="bn-sheet-doc">
                        <img
                          src={SHEETPRO_ICON}
                          alt=""
                          className="bn-sheet-icon bn-sheet-icon-img"
                          aria-hidden="true"
                        />
                        <div>
                          <input
                            className="bn-sheet-title"
                            readOnly
                            value="BrightNest — Follow-up sheet"
                            aria-label="Spreadsheet title"
                          />
                          <small>SheetPro · Parents never see this</small>
                        </div>
                      </div>
                      <div className="bn-sheet-share">
                        <span className="bn-sheet-count">{adminTodos.length} open</span>
                        <button type="button" className="bn-sheet-share-btn" disabled>
                          Share
                        </button>
                      </div>
                    </div>
                    <div className="bn-sheet-menuline" aria-hidden="true">
                      {["File", "Edit", "View", "Insert", "Format", "Data", "Tools", "Extensions", "Help"].map(
                        (m) => (
                          <span key={m}>{m}</span>
                        )
                      )}
                    </div>
                    <div className="bn-sheet-toolbar" aria-hidden="true">
                      <span className="bn-sheet-tool">100%</span>
                      <span className="bn-sheet-tool-sep" />
                      <span className="bn-sheet-tool">$</span>
                      <span className="bn-sheet-tool">%</span>
                      <span className="bn-sheet-tool-sep" />
                      <span className="bn-sheet-tool bn-sheet-tool-bold">B</span>
                      <span className="bn-sheet-tool">I</span>
                      <span className="bn-sheet-tool">
                        <u>U</u>
                      </span>
                    </div>
                    <div className="bn-sheet-formula">
                      <span className="bn-sheet-namebox">{sheetSel.addr}</span>
                      <span className="bn-sheet-fx">fx</span>
                      <div className="bn-sheet-fx-value" title={sheetSel.text}>
                        {sheetSel.text}
                      </div>
                    </div>
                    <div className="bn-sheet-scroll">
                      <table className="bn-sheet-grid">
                        <thead>
                          <tr>
                            <th className="bn-sheet-corner" />
                            {sheetCols.map((c) => (
                              <th key={c}>{c}</th>
                            ))}
                          </tr>
                          <tr className="bn-sheet-labels">
                            <th />
                            <th>Done?</th>
                            <th>Parent</th>
                            <th>Child</th>
                            <th>Parent question</th>
                            <th>What to follow up</th>
                            <th>Why flagged</th>
                            <th>Logged at</th>
                          </tr>
                        </thead>
                        <tbody>
                          {adminTodos.length === 0 ? (
                            <tr>
                              <td className="bn-sheet-rownum">1</td>
                              <td colSpan={7} className="bn-sheet-empty">
                                No open follow-ups. New rows appear when the assistant
                                couldn’t answer from centre materials.
                              </td>
                            </tr>
                          ) : (
                            adminTodos.map((t, i) => {
                              const row = i + 1;
                              const parentCell = t.parent_name || t.phone || "—";
                              const childCell = t.child_name || "—";
                              const qCell = t.question || "—";
                              const infoCell = t.info_needed || "";
                              const whyCell = reasonLabel(t.reason);
                              const whenCell = sheetWhen(t.created_at);
                              const active = (col) =>
                                sheetSel.addr === `${col}${row}` ? " bn-sheet-active" : "";
                              return (
                              <tr key={t.id}>
                                <td className="bn-sheet-rownum">{row}</td>
                                <td className={`bn-sheet-check${active("A")}`}>
                                  <input
                                    type="checkbox"
                                    title="Mark done"
                                    aria-label={`Mark follow-up for ${t.parent_name || t.id} done`}
                                    onChange={() => markTodoDone(t.id)}
                                    onFocus={() => selectSheetCell(`A${row}`, "Done?")}
                                  />
                                </td>
                                <td
                                  className={active("B").trim()}
                                  title={t.phone || ""}
                                  onClick={() =>
                                    selectSheetCell(`B${row}`, parentCell)
                                  }
                                >
                                  {parentCell}
                                </td>
                                <td
                                  className={active("C").trim()}
                                  onClick={() =>
                                    selectSheetCell(`C${row}`, childCell)
                                  }
                                >
                                  {childCell}
                                </td>
                                <td
                                  className={`bn-sheet-q${active("D")}`}
                                  title={qCell}
                                  onClick={() => selectSheetCell(`D${row}`, qCell)}
                                >
                                  {clip(qCell, 90)}
                                </td>
                                <td
                                  className={`bn-sheet-edit${active("E")}`}
                                  onClick={() =>
                                    selectSheetCell(`E${row}`, infoCell || "—")
                                  }
                                >
                                  <input
                                    type="text"
                                    defaultValue={infoCell}
                                    key={`${t.id}-${infoCell}`}
                                    placeholder="What should staff do next…"
                                    onFocus={() =>
                                      selectSheetCell(`E${row}`, infoCell || "—")
                                    }
                                    onChange={(e) =>
                                      selectSheetCell(`E${row}`, e.target.value || "—")
                                    }
                                    onBlur={(e) => {
                                      const next = e.target.value.trim();
                                      selectSheetCell(`E${row}`, next || "—");
                                      if (next !== (t.info_needed || "").trim()) {
                                        patchTodoField(t.id, { info_needed: next });
                                      }
                                    }}
                                  />
                                </td>
                                <td
                                  className={active("F").trim()}
                                  title={whyCell}
                                  onClick={() => selectSheetCell(`F${row}`, whyCell)}
                                >
                                  {clip(whyCell, 42)}
                                </td>
                                <td
                                  className={active("G").trim()}
                                  onClick={() => selectSheetCell(`G${row}`, whenCell)}
                                >
                                  {whenCell}
                                </td>
                              </tr>
                              );
                            })
                          )}
                        </tbody>
                      </table>
                    </div>
                    <div className="bn-sheet-tabs" aria-hidden="true">
                      <button type="button" className="active">
                        Follow-ups
                      </button>
                      <button type="button" disabled>
                        +
                      </button>
                    </div>
                  </div>
                ) : staffTab === "mail" ? (
                  <div className="bn-mail">
                    <header className="bn-mail-top">
                      <button type="button" className="bn-mail-menu" aria-hidden="true">
                        ☰
                      </button>
                      <div className="bn-mail-brand">
                        <img
                          src={MAILBOX_ICON}
                          alt=""
                          className="bn-mail-logo bn-mail-logo-img"
                          aria-hidden="true"
                        />
                        <strong>MailBox</strong>
                      </div>
                      <div className="bn-mail-search" aria-hidden="true">
                        <span className="bn-mail-search-ico">⌕</span>
                        Search mail
                      </div>
                      <span className="bn-mail-badge">
                        {unreadMailCount} unread
                      </span>
                    </header>
                    <div className="bn-mail-layout">
                      <nav className="bn-mail-nav" aria-label="Folders">
                        <button type="button" className="bn-mail-compose" disabled>
                          Compose
                        </button>
                        <button type="button" className="active">
                          <span>Inbox</span>
                          {unreadMailCount > 0 && <em>{unreadMailCount}</em>}
                        </button>
                        <button type="button" disabled>
                          Starred
                        </button>
                        <button type="button" disabled>
                          Snoozed
                        </button>
                        <button type="button" disabled>
                          Sent
                        </button>
                        <button type="button" disabled>
                          Drafts
                        </button>
                      </nav>
                      <div className="bn-mail-list" role="listbox" aria-label="Inbox">
                        <div className="bn-mail-list-toolbar" aria-hidden="true">
                          <span className="bn-mail-check-all" />
                          <span>Primary</span>
                          {unreadMailCount > 0 ? (
                            <em className="bn-mail-list-unread">{unreadMailCount}</em>
                          ) : null}
                        </div>
                        {staffMessages.length === 0 ? (
                          <p className="bn-mail-empty">
                            Your Primary tab is empty. New mail arrives when a parent
                            asks for staff, is upset, or raises a safety concern.
                          </p>
                        ) : (
                          staffMessages.map((m) => {
                            const active = selectedMsg?.id === m.id;
                            const from = m.parent_name || m.phone || "Unknown parent";
                            const subject = mailSubject(m);
                            const snip = mailSnippet(m);
                            const isUnread = !readMailIds.has(m.id) && !active;
                            return (
                              <button
                                key={m.id}
                                type="button"
                                role="option"
                                aria-selected={active}
                                className={`bn-mail-row ${isUnread ? "unread" : ""} ${
                                  active ? "active" : ""
                                } ${
                                  (m.priority || "") === "urgent" || m.priority === "high"
                                    ? "important"
                                    : ""
                                }`}
                                onClick={() => {
                                  setSelectedMsgId(m.id);
                                  setReadMailIds((prev) => {
                                    if (prev.has(m.id)) return prev;
                                    const next = new Set(prev);
                                    next.add(m.id);
                                    return next;
                                  });
                                }}
                              >
                                <span className="bn-mail-row-checks" aria-hidden="true">
                                  <span className="bn-mail-box" />
                                  <span className="bn-mail-star">☆</span>
                                </span>
                                <span className="bn-mail-row-from">{from}</span>
                                <span className="bn-mail-row-main">
                                  <span className="bn-mail-subj">{subject}</span>
                                  {snip ? (
                                    <span className="bn-mail-snip"> — {snip}</span>
                                  ) : null}
                                </span>
                                <time className="bn-mail-when">
                                  {mailWhen(m.created_at)}
                                </time>
                              </button>
                            );
                          })
                        )}
                      </div>
                      <article className="bn-mail-read">
                        {!selectedMsg ? (
                          <p className="bn-mail-empty bn-mail-empty-read">
                            No conversations selected
                          </p>
                        ) : (
                          <>
                            <div className="bn-mail-read-toolbar">
                              <button
                                type="button"
                                className="bn-mail-ack"
                                onClick={() => ackStaffMessage(selectedMsg.id)}
                              >
                                Archive / handled
                              </button>
                              <span className="bn-mail-read-meta">
                                {priorityLabel(
                                  selectedMsg.priority,
                                  selectedMsg.priority_label
                                )}
                                {" · "}
                                {categoryLabel(
                                  selectedMsg.category,
                                  selectedMsg.category_label
                                )}
                              </span>
                            </div>
                            <h2 className="bn-mail-subject">{mailSubject(selectedMsg)}</h2>
                            <div className="bn-mail-from-block">
                              <span className="bn-mail-avatar lg bn-mail-avatar-bot" aria-hidden="true">
                                BN
                              </span>
                              <div className="bn-mail-from-meta">
                                <div className="bn-mail-from-top">
                                  <strong>BrightNest chatbot</strong>
                                  <time>{sheetWhen(selectedMsg.created_at)}</time>
                                </div>
                                <div className="bn-mail-from-sub">
                                  to Centre desk
                                  {selectedMsg.parent_name
                                    ? ` · re: ${selectedMsg.parent_name}`
                                    : ""}
                                  {selectedMsg.phone ? (
                                    <span> · {selectedMsg.phone}</span>
                                  ) : null}
                                  {selectedMsg.child_name
                                    ? ` · Child: ${selectedMsg.child_name}`
                                    : ""}
                                </div>
                              </div>
                            </div>
                            <div className="bn-mail-body">
                              <WaMarkdown className="bn-mail-email bn-md">
                                {mailEmailBody(selectedMsg)}
                              </WaMarkdown>
                            </div>
                          </>
                        )}
                      </article>
                    </div>
                  </div>
                ) : (
                  <div className="bn-families">
                    <header className="bn-families-top">
                      <div className="bn-families-brand">
                        <img src={FAMILIES_ICON} alt="" className="bn-families-logo" />
                        <div>
                          <strong>Families</strong>
                          <small>WhatsApp number → parent → kids → upcoming classes</small>
                        </div>
                      </div>
                      <button
                        type="button"
                        className="bn-ghost"
                        onClick={refreshFamilies}
                      >
                        Refresh
                      </button>
                    </header>
                    <div className="bn-families-layout">
                      <div className="bn-families-list" role="listbox" aria-label="Families">
                        {familiesRoster.length === 0 ? (
                          <p className="bn-empty">No families on the sheet yet.</p>
                        ) : (
                          familiesRoster.map((f) => {
                            const active = selectedFamilyPhone === f.phone;
                            const kids = (f.children || [])
                              .map((c) => c.child_name)
                              .filter(Boolean);
                            return (
                              <button
                                key={f.phone}
                                type="button"
                                role="option"
                                aria-selected={active}
                                className={`bn-families-row ${active ? "active" : ""}`}
                                onClick={() => setSelectedFamilyPhone(f.phone)}
                              >
                                <strong>{f.parent_name || "Unknown parent"}</strong>
                                <span>{f.phone}</span>
                                <em>
                                  {kids.length
                                    ? kids.join(", ")
                                    : "No children listed"}
                                </em>
                              </button>
                            );
                          })
                        )}
                      </div>
                      <article className="bn-families-detail">
                        {(() => {
                          const fam =
                            familiesRoster.find((f) => f.phone === selectedFamilyPhone) ||
                            familiesRoster[0];
                          if (!fam) {
                            return (
                              <p className="bn-empty">Select a family to inspect records.</p>
                            );
                          }
                          return (
                            <>
                              <h2>{fam.parent_name || "Unknown parent"}</h2>
                              <p className="bn-families-phone">
                                WhatsApp <code>{fam.phone}</code>
                              </p>
                              <p className="bn-note">
                                Source: <code>families.csv</code> · upcoming from
                                enrolments + chat bookings
                              </p>
                              {(fam.children || []).map((child) => (
                                <div key={child.child_name} className="bn-families-child">
                                  <h3>{child.child_name}</h3>
                                  {child.preferred_courses?.length ? (
                                    <p>
                                      Preferred:{" "}
                                      {child.preferred_courses.join(", ")}
                                    </p>
                                  ) : null}
                                  {child.notes ? (
                                    <p className="bn-muted">{child.notes}</p>
                                  ) : null}
                                  <h4>
                                    Upcoming ({child.upcoming_count || 0})
                                  </h4>
                                  {(child.upcoming || []).length === 0 ? (
                                    <p className="bn-empty">No upcoming classes on file.</p>
                                  ) : (
                                    <ul className="bn-families-upcoming">
                                      {(child.upcoming || []).slice(0, 12).map((u, i) => (
                                        <li key={`${child.child_name}-${u.slot_start}-${i}`}>
                                          <strong>{u.course_label || u.course_key}</strong>
                                          <span>{u.slot_display}</span>
                                          <span>
                                            {u.teacher_name || "—"}
                                            {u.room ? ` · ${u.room}` : ""}
                                            {u.kind === "chat_booking" ? " · chat" : ""}
                                          </span>
                                        </li>
                                      ))}
                                    </ul>
                                  )}
                                </div>
                              ))}
                            </>
                          );
                        })()}
                      </article>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
          </section>
        </div>
      </div>
    </div>
  );
}
