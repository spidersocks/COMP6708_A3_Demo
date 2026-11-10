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
const SHEETPRO_ICON = "/projects/brightnest/sheetpro.webp";
const MAILBOX_ICON = "/projects/brightnest/mailbox.webp";
/** How many calendar days the Class register loads (API max 21). */
const SCHEDULE_DAYS = 14;

const DEMO_PROBES = [
  { label: "Hours", text: "What are your opening hours?" },
  { label: "Christmas", text: "Are you open on Christmas Day?" },
  { label: "Typhoon", text: "What happens when there is a typhoon signal 8?" },
  { label: "Phonics fee", text: "How much is phonics?" },
  { label: "RAG · Private 1:1", text: "Do you offer private 1:1 tutoring?" },
  { label: "Teachers", text: "Who teaches Mandarin?" },
  { label: "Location", text: "Where are you located?" },
  { label: "Friend guest", text: "Can I bring my friend to the trial class?" },
  { label: "Phonics seats", text: "How many seats are left in Saturday phonics?" },
  { label: "Trial slots", text: "Show trial class availability" },
  { label: "Book Jason", text: "Can I book a class for Jason next week?" },
  { label: "Cancel class", text: "Can I cancel Jason's class next Tuesday?" },
  { label: "Unknown ask", text: "Does BrightNest offer scuba diving for toddlers?" },
  { label: "Off-topic", text: "What's the weather in Tokyo tomorrow?" },
  { label: "Talk to staff", text: "Can I speak to a real person please?" },
  { label: "Angry parent", text: "This is unacceptable — I'm furious about today's class!" },
  {
    label: "Safety call",
    text: "My child has a peanut allergy and had a reaction after class yesterday — please call me ASAP",
  },
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
  if (route === "deterministic_opening_hours" || route === "opening_hours") {
    return { label: "Opening-hours engine", tone: "hours" };
  }
  if (route === "severe_weather") {
    return { label: "Severe-weather policy tool", tone: "hours" };
  }
  if (route === "fees" || route === "payment" || route === "location") {
    return { label: `Structured fact · ${route}`, tone: "hours" };
  }
  if (
    route === "illness" ||
    route === "allergy" ||
    route === "teachers" ||
    route === "pickup" ||
    route === "makeup" ||
    route === "sibling" ||
    route === "waiting" ||
    route === "guest_policy" ||
    route === "pets_policy" ||
    route === "trial_info"
  ) {
    return { label: `Structured fact · ${route}`, tone: "hours" };
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
  if (route === "off_domain") {
    return { label: "Off-domain refuse", tone: "idle" };
  }
  if (route) {
    return { label: String(route), tone: "idle" };
  }
  return { label: "Waiting for a query", tone: "idle" };
}

function RagHit({ hit, index }) {
  const [open, setOpen] = useState(false);
  const rank = hit.rank ?? index + 1;
  const body = hit.text || hit.snippet || "";
  const score = scoreLabel(hit.score);
  return (
    <li className={`bn-hit ${open ? "is-open" : ""}`}>
      <button
        type="button"
        className="bn-hit-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <div className="bn-hit-head">
          <strong>
            <span className="bn-hit-rank">#{rank}</span>{" "}
            {hit.title || hit.canonical || hit.source || `Hit ${rank}`}
          </strong>
          {score && <code title="retrieval score">{score}</code>}
        </div>
        <span className="bn-hit-src">{hit.source}</span>
        {!open && body ? <p className="bn-hit-snip">{body}</p> : null}
        <span className="bn-hit-expand-hint">{open ? "Collapse chunk" : "Expand full chunk"}</span>
      </button>
      {open && body ? (
        <pre className="bn-hit-full">{body}</pre>
      ) : null}
      {open && hit.char_count != null ? (
        <div className="bn-hit-meta">{hit.char_count} chars · score {score ?? "—"}</div>
      ) : null}
    </li>
  );
}

function QueryHighlight({ segments, fallback, cue }) {
  const parts =
    Array.isArray(segments) && segments.length > 0
      ? segments
      : fallback
        ? [{ text: fallback, hit: false }]
        : [];
  if (!parts.length) return <p className="bn-empty">No query text.</p>;
  return (
    <p className="bn-query-hl" title={cue ? `Cue: ${cue}` : undefined}>
      {parts.map((seg, i) =>
        seg.hit ? (
          <mark key={`h-${i}`} className="bn-query-cue">
            {seg.text}
          </mark>
        ) : (
          <span key={`t-${i}`}>{seg.text}</span>
        )
      )}
    </p>
  );
}

function MatchSummaryCard({ summary }) {
  if (!summary || (!summary.headline && !summary.query)) return null;
  return (
    <div className="bn-match-summary">
      <div className="bn-match-summary-kicker">What matched in this query</div>
      <p className="bn-match-headline">{summary.headline}</p>
      <QueryHighlight
        segments={summary.segments}
        fallback={summary.query || summary.annotated}
        cue={summary.cue}
      />
      {summary.cue ? (
        <p className="bn-match-cue-line">
          Exact cue: <code>«{summary.cue}»</code>
          {summary.rule_label ? (
            <>
              {" "}
              → <strong>{summary.rule_label}</strong> (<code>{summary.route}</code>)
            </>
          ) : null}
        </p>
      ) : null}
      {summary.explanation ? (
        <p className="bn-match-explain">{summary.explanation}</p>
      ) : null}
      {summary.downstream ? (
        <p className="bn-match-down">
          Downstream: {summary.downstream}
        </p>
      ) : null}
    </div>
  );
}

function RuleEvidence({ rule, defaultOpen = false, isWinner = false }) {
  const [open, setOpen] = useState(Boolean(defaultOpen));
  useEffect(() => {
    setOpen(Boolean(defaultOpen));
  }, [defaultOpen, rule?.id]);

  const flag = rule.matched
    ? rule.suppressed_by_conflict
      ? "cleared"
      : isWinner
        ? "winner"
        : "fired"
    : "idle";
  const reason =
    rule.status_reason ||
    (rule.matched ? rule.why : rule.why_not) ||
    rule.detail ||
    "";
  const checks = rule.checks || [];
  const evidence = rule.match_evidence || {};
  const cue =
    evidence.cue ||
    rule.matched_span?.text ||
    "";
  const queryText = rule.query || evidence.full_query || "";
  const segments = evidence.segments || [];

  return (
    <li
      className={
        (rule.matched
          ? rule.suppressed_by_conflict
            ? "bn-rule-suppressed"
            : isWinner
              ? "bn-rule-winner"
              : "bn-rule-hit"
          : "bn-rule-miss") + (open ? " bn-rule-open" : "")
      }
    >
      <button
        type="button"
        className="bn-rule-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="bn-rule-flag">{flag}</span>
        <div className="bn-rule-main">
          <strong>
            {rule.label}
            {isWinner ? <em className="bn-rule-winner-tag"> · winning</em> : null}
          </strong>
          <code>{rule.id}</code>
          {rule.score != null ? <em>score {Number(rule.score).toFixed(2)}</em> : null}
          {rule.score_origin?.source && rule.score_origin.source !== "none" ? (
            <p className="bn-rule-score-origin">
              score from <code>{rule.score_origin.source}</code>
              {rule.score_origin.why ? ` — ${rule.score_origin.why}` : ""}
            </p>
          ) : null}
          {cue ? (
            <p className="bn-rule-cue-chip">
              cue <code>«{cue}»</code>
            </p>
          ) : null}
          {reason ? <p className="bn-rule-reason">{reason}</p> : null}
          <span className="bn-hit-expand-hint">
            {open ? "Hide match detail" : "Show what matched"}
          </span>
        </div>
      </button>
      {open ? (
        <div className="bn-rule-why">
          <div className="bn-rule-block bn-rule-block-query">
            <span>Parent message · matched cue highlighted</span>
            <QueryHighlight
              segments={segments}
              fallback={queryText || evidence.annotated}
              cue={cue}
            />
            {cue ? (
              <p className="bn-rule-cue-meta">
                Exact cue <code>«{cue}»</code>
                {evidence.start != null && evidence.end != null
                  ? ` at characters [${evidence.start}:${evidence.end}]`
                  : ""}
                {evidence.kind ? ` · kind=${evidence.kind}` : ""}
              </p>
            ) : (
              <p className="bn-rule-cue-meta">
                No single substring cue — matched by{" "}
                {evidence.kind || "predicate / session state"}.
              </p>
            )}
          </div>
          {evidence.explanation ? (
            <div className="bn-rule-block">
              <span>Why this cue matters</span>
              <p>{evidence.explanation}</p>
            </div>
          ) : null}
          {rule.matched && rule.why ? (
            <div className="bn-rule-block">
              <span>Why this rule fired</span>
              <p>{rule.why}</p>
            </div>
          ) : null}
          {!rule.matched || rule.suppressed_by_conflict ? (
            rule.why_not ? (
              <div className="bn-rule-block">
                <span>
                  {rule.suppressed_by_conflict
                    ? "Why it was cleared after matching"
                    : "Why it stayed idle"}
                </span>
                <p>{rule.why_not}</p>
              </div>
            ) : null
          ) : null}
          {rule.score_origin?.source && rule.score_origin.source !== "none" ? (
            <div className="bn-rule-block">
              <span>Where this score came from</span>
              <p>
                <code>{rule.score_origin.source}</code>
                {rule.score_origin.score != null
                  ? ` = ${Number(rule.score_origin.score).toFixed(3)}`
                  : ""}
                {" — "}
                {rule.score_origin.why || "see score ledger"}
              </p>
              <p className="bn-rule-cue-meta">
                Intent scores are <strong>not</strong> RAG similarity. Hard detectors
                award fixed values (e.g. fees=0.90); prototypes may blend; conflicts
                clear losers.
              </p>
            </div>
          ) : null}
          {rule.pattern_summary ? (
            <div className="bn-rule-block">
              <span>Detector looks for</span>
              <p>{rule.pattern_summary}</p>
            </div>
          ) : null}
          {rule.mechanism ? (
            <div className="bn-rule-block">
              <span>How this rule works in code</span>
              <p>{rule.mechanism}</p>
            </div>
          ) : null}
          {checks.length > 0 ? (
            <div className="bn-rule-block">
              <span>Checks run on this message</span>
              <ul className="bn-rule-checks">
                {checks.map((c, i) => (
                  <li key={`${rule.id}-c-${i}`} className={c.passed ? "pass" : "fail"}>
                    <strong>{c.passed ? "pass" : "fail"}</strong>
                    <span>
                      {c.label}
                      {c.detail ? ` — ${c.detail}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {rule.downstream ? (
            <div className="bn-rule-block">
              <span>If this rule wins</span>
              <p>{rule.downstream}</p>
            </div>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function parseStamp(iso) {
  if (!iso) return null;
  const raw = String(iso).trim();
  if (/[A-Za-z]/.test(raw) && !raw.includes("T")) {
    const d = new Date(raw);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  try {
    const d = new Date(raw);
    return Number.isNaN(d.getTime()) ? null : d;
  } catch {
    return null;
  }
}

function sheetWhen(iso) {
  if (!iso) return "—";
  const raw = String(iso).trim();
  // Already staff-facing stamps from the CSV (e.g. "2 Nov 2026, 2:26 pm")
  if (/[A-Za-z]/.test(raw) && !raw.includes("T")) return raw;
  const d = parseStamp(raw);
  if (!d) return raw;
  return d.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Compact Gmail-style stamp for inbox rows */
function mailWhen(iso) {
  if (!iso) return "—";
  const raw = String(iso).trim();
  const d = parseStamp(raw);
  if (!d) {
    if (/[A-Za-z]/.test(raw) && !raw.includes("T")) {
      // "2 Nov 2026, 5:35 pm" → keep time if present, else short date
      const time = raw.match(/(\d{1,2}:\d{2}\s*[ap]m)/i);
      if (time) return time[1].replace(/\s+/g, " ");
      return raw.replace(/,?\s*\d{4}/, "");
    }
    return raw;
  }
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) {
    return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }
  if (d.getFullYear() === now.getFullYear()) {
    return d.toLocaleDateString([], { month: "short", day: "numeric" });
  }
  return d.toLocaleDateString([], { month: "short", day: "numeric", year: "2-digit" });
}

function clip(text, n = 72) {
  const s = (text || "").trim();
  if (s.length <= n) return s;
  return `${s.slice(0, n - 1)}…`;
}

function initials(name) {
  const parts = String(name || "")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (!parts.length) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
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
  const [rightView, setRightView] = useState("system"); // system | staff
  const [staffTab, setStaffTab] = useState("sheet"); // sheet | mail
  const [selectedMsgId, setSelectedMsgId] = useState(null);
  const [readMailIds, setReadMailIds] = useState(() => new Set());
  const [sheetSel, setSheetSel] = useState({
    addr: "A1",
    text: "Follow-up sheet",
  });

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
    refreshSchedule();
    refreshAdminTodos();
    refreshStaffMessages();
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
      setLastBooking(null);
      setPendingSlot(null);
      setTrace(null);
      setAdminTodos([]);
      setStaffMessages([]);
      setSelectedMsgId(null);
      setReadMailIds(new Set());
      setRightView("system");
      setStaffTab("sheet");
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
          slots: data.slots || null,
          provider: data.provider,
        },
      ]);
      // Dialogue / tool bookings mutate chat_bookings.csv — keep the register in sync
      if (
        data.booking ||
        nextTrace.route === "booking_dialogue" ||
        nextTrace.route === "booking_tool" ||
        nextTrace.route === "cancel_dialogue"
      ) {
        refreshSchedule();
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
      // Refresh admin register so seats_left / later dates update in the inspector
      refreshSchedule();
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
  const grounding = trace?.grounding;
  const enrichMode = rag?.mode === "grounded_enrich" || grounding?.used_rag;
  // Full RAG generate hits, or enrichment hits under a privileged structured card.
  const hits =
    route === "rag" || enrichMode ? rag?.hits || [] : [];

  const turnKey = String(trace?.latency_ms ?? "idle");
  const hoursOpen =
    route === "deterministic_opening_hours" ||
    route === "opening_hours" ||
    route === "severe_weather";
  const ragOpen = route === "rag" || Boolean(enrichMode);
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
    ? (() => {
        const days = schedule.days || [];
        const first = days[0]?.label || "";
        const last = days[days.length - 1]?.label || "";
        const n = scheduleDays.reduce((acc, d) => acc + (d.sessions?.length || 0), 0);
        const span = first && last ? `${first} → ${last}` : `${days.length}d`;
        return `${teachers.length} teachers · ${n} sessions · ${span}`;
      })()
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
    if (enrichMode) {
      const n = rag?.hit_count ?? hits.length;
      const top = scoreLabel(rag?.top_score ?? hits[0]?.score);
      return `Enrich · ${n}/${rag?.top_k ?? "k"} · top ${top ?? "—"} · card wins`;
    }
    if (route && route !== "rag" && !hits.length) {
      return `Skipped · route=${route}`;
    }
    if (!rag && !hits.length) return "Idle until a KB question";
    const n = rag?.hit_count ?? hits.length;
    const top = scoreLabel(rag?.top_score ?? hits[0]?.score);
    return `${n}/${rag?.top_k ?? "k"} · idx ${rag?.index_size ?? "—"} · top ${top ?? "—"}`;
  }, [rag, hits, route, enrichMode]);

  const routing = trace?.routing;
  const decisionPath = routing?.decision_path || routing?.debug?.decision_path || [];
  const rulesEvaluated =
    routing?.rules_evaluated || routing?.debug?.rules_evaluated || [];
  const winningRule = routing?.winning_rule || routing?.debug?.winning_rule || {};
  const matchSummary = routing?.match_summary || routing?.debug?.match_summary || {};
  const scoreLedger = routing?.score_ledger || routing?.debug?.score_ledger || [];
  const scoreGuide =
    routing?.score_source_guide || routing?.debug?.score_source_guide || [];
  const arbiter = routing?.debug?.arbiter || {};
  const winningRuleId = matchSummary?.rule_id || winningRule?.route || route;
  const groundingSummary = grounding
    ? grounding.mode === "grounded_compose"
      ? `compose · ${grounding.rag_hit_count || 0} KB hits · card privileged`
      : grounding.mode === "bypass"
        ? `bypass · ${grounding.why ? "RAG skipped" : "tool"}`
        : grounding.mode || "—"
    : "—";
  const routerSummary = useMemo(() => {
    if (!route) return "Idle until a query";
    const arbReason = winningRule?.arbiter_reason || arbiter?.reason;
    const arbBit = arbiter?.called
      ? `arbiter ${arbReason || "reviewed"}`
      : arbReason
        ? `arbiter ${arbReason}`
        : winningRule?.score != null
          ? `score ${Number(winningRule.score).toFixed(2)}`
          : "";
    return `${route}${arbBit ? ` · ${arbBit}` : ""}`;
  }, [route, winningRule, arbiter]);

  const historyTurns = trace?.history?.turns || [];
  const memSummary = useMemo(() => {
    const n = trace?.history?.turns_loaded ?? historyTurns.length ?? chatHistory.length;
    const lim = trace?.history?.limit ?? 10;
    return `${n}/${lim} msgs · click to expand`;
  }, [trace?.history, historyTurns.length, chatHistory.length]);

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

        {/* Inspector / Staff desk */}
        <section className="bn-inspector">
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

          {rightView === "staff" ? (
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
                </div>
              </div>
              <div className="bn-browser-url">
                <span className="bn-browser-lock" aria-hidden="true" />
                <code>
                  {staffTab === "sheet"
                    ? "sheetpro.app/d/brightnest-followups"
                    : "mailbox.app/u/0/#inbox"}
                </code>
              </div>

              <div className="bn-browser-page">
                {staffTab === "sheet" ? (
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
                ) : (
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
                )}
              </div>
            </div>
          ) : (
            <>
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
            key={`router-${turnKey}`}
            title="Router decision"
            summary={routerSummary}
            defaultOpen={Boolean(trace)}
          >
            {!trace ? (
              <p className="bn-empty">Send a probe to see which rules fired and why.</p>
            ) : (
              <>
                <MatchSummaryCard summary={matchSummary} />

                {grounding ? (
                  <div className="bn-grounding-card">
                    <div className="bn-match-summary-kicker">Answer grounding</div>
                    <p className="bn-match-headline">
                      Mode <code>{grounding.mode}</code>
                      {grounding.structured_privileged ? " · docs privileged" : ""}
                      {grounding.live_doc ? " · live doc attached" : ""}
                    </p>
                    <p className="bn-match-explain">{grounding.why}</p>
                    <p className="bn-match-down">
                      Summary: {groundingSummary}
                      {grounding.composed ? " · composed with KB" : ""}
                      {grounding.ask_tool_ok
                        ? grounding.invoke_tool === false
                          ? " · tool gate SKIP (no seat cards)"
                          : " · tool gate OK (seat cards exposed)"
                        : ""}
                    </p>
                  </div>
                ) : null}

                <ul className="bn-fact-list">
                  <li>
                    <span>Final route</span>
                    <code>{route || "—"}</code>
                  </li>
                  <li>
                    <span>Winning score</span>
                    <code>
                      {winningRule?.score != null
                        ? Number(winningRule.score).toFixed(3)
                        : "—"}
                    </code>
                  </li>
                  <li>
                    <span>Pre-arbiter pick</span>
                    <code>{winningRule?.pre_arbiter_route || route || "—"}</code>
                  </li>
                  <li>
                    <span>Cheap LLM review</span>
                    <code>
                      {arbiter?.called
                        ? `${arbiter.action || "review"} · ${arbiter.reason || ""}`
                        : `skipped · ${
                            arbiter?.skip_detail ||
                            arbiter?.reason ||
                            winningRule?.arbiter_skip_detail ||
                            "n/a"
                          }`}
                    </code>
                  </li>
                  {winningRule?.override ? (
                    <li>
                      <span>Arbiter override</span>
                      <code>
                        {winningRule.override.from} → {winningRule.override.to}
                      </code>
                    </li>
                  ) : null}
                  {trace?.adapt ? (
                    <li>
                      <span>Fact rewrite</span>
                      <code>
                        {trace.adapt.ran
                          ? `yes · ${trace.adapt.model || "main LLM"}`
                          : `no · ${trace.adapt.why || "passthrough"}`}
                      </code>
                    </li>
                  ) : null}
                  <li>
                    <span>RAG this turn</span>
                    <code>{route === "rag" ? "ran" : `skipped (${route})`}</code>
                  </li>
                </ul>

                {trace?.adapt?.why ? (
                  <p className="bn-note">{trace.adapt.why}</p>
                ) : null}

                {decisionPath.length > 0 && (
                  <div className="bn-path">
                    <div className="bn-path-label">Exact path taken</div>
                    <ol>
                      {decisionPath.map((p, i) => (
                        <li key={`${p.step}-${i}`}>
                          <strong>{p.summary}</strong>
                          {p.detail ? <span>{p.detail}</span> : null}
                        </li>
                      ))}
                    </ol>
                  </div>
                )}

                {rulesEvaluated.length > 0 && (
                  <details className="bn-rules" open={Boolean(route)}>
                    <summary>
                      Rule map ·{" "}
                      {rulesEvaluated.filter((r) => r.matched).length} fired /{" "}
                      {rulesEvaluated.length} checked
                      {matchSummary?.cue
                        ? ` · winning cue «${matchSummary.cue}»`
                        : ""}
                    </summary>
                    <p className="bn-rules-hint">
                      Click any rule to see the parent message with the exact
                      matched substring highlighted, plus every check that
                      passed or failed.
                    </p>
                    <ul className="bn-rule-list">
                      {rulesEvaluated.map((r) => (
                        <RuleEvidence
                          key={r.id}
                          rule={r}
                          isWinner={r.id === winningRuleId && Boolean(r.matched)}
                          defaultOpen={r.id === winningRuleId}
                        />
                      ))}
                    </ul>
                  </details>
                )}

                {routing?.scores && Object.keys(routing.scores).length > 0 && (
                  <details className="bn-rules" open>
                    <summary>
                      Intent scores · where the numbers come from
                    </summary>
                    <p className="bn-rules-hint">
                      These are <strong>not</strong> RAG retrieval similarities.
                      Hard detectors award fixed scores (e.g. allergy=0.95,
                      fees=0.90). Soft prototypes may blend. Conflicts clear
                      losers. RAG only gets a floor when nothing else owns the turn.
                    </p>
                    {scoreGuide.length > 0 && (
                      <ul className="bn-score-guide">
                        {scoreGuide.map((g) => (
                          <li key={g.id}>
                            <code>{g.id}</code>
                            <strong>{g.label}</strong>
                            <span>{g.meaning}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                    <ul className="bn-fact-list">
                      {Object.entries(routing.scores)
                        .sort((a, b) => Number(b[1]) - Number(a[1]))
                        .map(([k, v]) => {
                          const origin =
                            routing.score_origins?.[k] ||
                            scoreLedger.filter((e) => e.route === k).slice(-1)[0];
                          return (
                            <li key={k}>
                              <span>
                                {k}
                                {origin?.source ? (
                                  <em className="bn-score-src"> · {origin.source}</em>
                                ) : null}
                              </span>
                              <code>{Number(v).toFixed(3)}</code>
                            </li>
                          );
                        })}
                    </ul>
                    {scoreLedger.length > 0 && (
                      <details className="bn-rules bn-ledger">
                        <summary>
                          Score ledger · {scoreLedger.length} steps (award / blend / clear)
                        </summary>
                        <ol className="bn-score-ledger">
                          {scoreLedger.map((e, i) => (
                            <li key={`led-${i}`} className={`bn-led-${e.op || "note"}`}>
                              <strong>{e.op || "note"}</strong>
                              <span>
                                <code>{e.route}</code>
                                {e.score != null ? ` = ${Number(e.score).toFixed(3)}` : ""}
                                {e.prev_score != null
                                  ? ` (was ${Number(e.prev_score).toFixed(3)})`
                                  : ""}
                                {" · "}
                                {e.source}
                                {e.why ? ` — ${e.why}` : ""}
                                {e.cue ? ` · cue «${e.cue}»` : ""}
                              </span>
                            </li>
                          ))}
                        </ol>
                      </details>
                    )}
                  </details>
                )}
              </>
            )}
          </Reveal>

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

          <Reveal
            key={`rag-${turnKey}`}
            title={enrichMode ? "RAG enrichment (card privileged)" : "RAG retrieval"}
            summary={ragSummary}
            defaultOpen={ragOpen}
          >
            {hits.length === 0 ? (
              <div className="bn-rag-skip">
                <p className="bn-empty">
                  {route === "rag"
                    ? "No chunks returned."
                    : route
                      ? `Full RAG generate did not run — router chose “${route}” (hard bypass or empty enrich).`
                      : "Idle until a KB question."}
                </p>
                {route && route !== "rag" ? (
                  <p className="bn-note">
                    Soft routes (allergy, fees, teachers…) retrieve KB as{" "}
                    <strong>enrichment</strong> under a privileged structured card.
                    Hard tools (typhoon, seats, booking, escalation) skip retrieve.
                    For pure retrieve→generate, use <strong>RAG · Private 1:1</strong>.
                  </p>
                ) : null}
              </div>
            ) : (
              <>
                {enrichMode ? (
                  <p className="bn-note">
                    These chunks are <strong>secondary</strong>. The structured{" "}
                    <code>{route}</code> fact card remains source of truth; the model
                    may elaborate from KB but must not override fees, times, or policy.
                  </p>
                ) : null}
                <div className="bn-rag-strip">
                  <span>index size {rag?.index_size ?? "—"}</span>
                  <span>top_k {rag?.top_k ?? hits.length}</span>
                  <span>hits returned {rag?.hit_count ?? hits.length}</span>
                  <span>top score {scoreLabel(rag?.top_score ?? hits[0]?.score) ?? "—"}</span>
                  <span>embed {rag?.embedding_mode || "—"}</span>
                  {enrichMode ? <span>mode grounded_enrich</span> : null}
                  {rag?.language_fallback ? <span>lang fallback</span> : null}
                  {rag?.gap_reason ? <span>gap {rag.gap_reason}</span> : null}
                </div>
                <ul className="bn-hits">
                  {hits.map((h, i) => (
                    <RagHit key={`${h.source}-${h.rank || i}`} hit={h} index={i} />
                  ))}
                </ul>
              </>
            )}
          </Reveal>

          <Reveal
            key={`register-${turnKey}`}
            title="Class register"
            summary={scheduleSummary}
            defaultOpen={bookingOpen}
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
                    : trace?.route === "booking_tool"
                      ? `live seats · ${trace?.booking?.slot_count ?? "open"} slots`
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
            summary={memSummary}
            defaultOpen={false}
          >
            {historyTurns.length > 0 ? (
              <ul className="bn-mem-turns">
                {historyTurns.map((t, i) => (
                  <li key={`mem-${i}`}>
                    <details>
                      <summary>
                        <strong>{t.role === "user" || t.role === "parent" ? "parent" : "bot"}</strong>
                        <span>{clip(t.content || "", 100)}</span>
                      </summary>
                      <pre className="bn-hit-full">{t.content || ""}</pre>
                    </details>
                  </li>
                ))}
              </ul>
            ) : chatHistory.length > 0 ? (
              <ul className="bn-mem-turns">
                {chatHistory.map((m, i) => (
                  <li key={`local-mem-${i}`}>
                    <details>
                      <summary>
                        <strong>{m.role === "user" ? "parent" : "bot"}</strong>
                        <span>{clip(m.message || "", 100)}</span>
                      </summary>
                      <pre className="bn-hit-full">{m.message || ""}</pre>
                    </details>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="bn-empty">No prior turns yet.</p>
            )}
            {trace?.history?.preview ? (
              <details className="bn-rules">
                <summary>Raw context string fed to the model</summary>
                <pre className="bn-pre bn-pre-tall">{trace.history.preview}</pre>
              </details>
            ) : null}
          </Reveal>

          <Reveal
            key={`probes-${turnKey}`}
            title="Demo probes"
            summary="Quick paths for the live talk"
            defaultOpen
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
            </>
          )}
        </section>
      </div>
    </div>
  );
}
