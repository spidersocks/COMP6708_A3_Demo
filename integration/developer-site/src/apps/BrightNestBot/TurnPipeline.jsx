import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

const LLM_ROLE_LABELS = {
  intent_arbiter: "Intent arbiter (cheap)",
  grounded_compose: "Grounded compose (main)",
  fact_adapt: "Fact rewrite (main)",
  rag_generate: "RAG generate (main)",
  gap_polish: "Gap polish (cheap)",
  cancel_pick: "Cancel pick (cheap)",
  booking_parse: "Booking parse (cheap)",
  query_rewrite: "Query rewrite (cheap)",
  staff_email: "Staff email draft (cheap)",
  main_generate: "Main generate",
  cheap_llm: "Cheap LLM",
  cheap_json: "Cheap JSON",
};

/**
 * Compact top-down layout (world px, origin at spine).
 * Spine: parent → language → memory → rules → router → path → FSM|RAG → …
 * Scores before the route pick; live data is a side arm.
 */
const LAYOUT = {
  parent: { x: 0, y: 0, w: 156, h: 42 },
  lang: { x: 0, y: 72, w: 148, h: 42 },
  memory: { x: 0, y: 150, w: 132, h: 58 },
  rules: { x: 0, y: 240, w: 146, h: 46 },
  router: { x: 0, y: 330, w: 146, h: 46 },
  arbiter: { x: 178, y: 330, w: 120, h: 46 },
  path: { x: 0, y: 430, w: 112, h: 112 },
  fsm: { x: -168, y: 560, w: 140, h: 46 },
  rag: { x: 168, y: 560, w: 140, h: 46 },
  live: { x: -168, y: 650, w: 140, h: 46 },
  rewrite: { x: 168, y: 650, w: 148, h: 46 },
  chroma: { x: 168, y: 740, w: 124, h: 58 },
  sheets: { x: -168, y: 840, w: 132, h: 58 },
  llm: { x: 40, y: 840, w: 156, h: 46 },
  answer: { x: 0, y: 940, w: 156, h: 42 },
};

const EDGES = [
  { id: "e-parent-lang", from: "parent", to: "lang" },
  { id: "e-lang-mem", from: "lang", to: "memory" },
  { id: "e-mem-rules", from: "memory", to: "rules" },
  { id: "e-rules-router", from: "rules", to: "router" },
  { id: "e-router-path", from: "router", to: "path" },
  { id: "e-router-arb", from: "router", to: "arbiter", dashed: true },
  { id: "e-arb-path", from: "arbiter", to: "path", dashed: true },
  { id: "e-path-fsm", from: "path", to: "fsm" },
  { id: "e-path-rag", from: "path", to: "rag" },
  { id: "e-fsm-live", from: "fsm", to: "live", dashed: true },
  { id: "e-fsm-llm", from: "fsm", to: "llm" },
  { id: "e-fsm-sheets", from: "fsm", to: "sheets" },
  { id: "e-rag-rewrite", from: "rag", to: "rewrite" },
  { id: "e-rewrite-chroma", from: "rewrite", to: "chroma" },
  { id: "e-chroma-llm", from: "chroma", to: "llm" },
  { id: "e-live-llm", from: "live", to: "llm", dashed: true },
  { id: "e-live-rewrite", from: "live", to: "rewrite", dashed: true },
  { id: "e-llm-answer", from: "llm", to: "answer" },
  { id: "e-sheets-answer", from: "sheets", to: "answer" },
];

const WORLD = { minX: -340, minY: -28, maxX: 340, maxY: 1000 };
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 2.0;

function llmRoleLabel(role) {
  const raw = String(role || "");
  if (LLM_ROLE_LABELS[raw]) return LLM_ROLE_LABELS[raw];
  if (raw.startsWith("final_check:")) {
    return `Final check · ${raw.slice("final_check:".length)}`;
  }
  return raw || "LLM call";
}

function scoreLabel(score) {
  if (score == null || Number.isNaN(Number(score))) return null;
  return Number(score).toFixed(3);
}

function clip(text, n = 120) {
  const s = (text || "").trim();
  if (s.length <= n) return s;
  return `${s.slice(0, n - 1)}…`;
}

/** FSM (tools / sticky dialogue) vs RAG (retrieve → generate). */
function pathKind(route, grounding) {
  const g = grounding?.mode;
  if (g === "pure_rag" || route === "rag") return "rag";
  if (route) return "fsm";
  return null;
}

/** Live arm: HKO/hours facts, register seats, weather feed, etc. */
function liveUsed(route, grounding, facts) {
  return Boolean(
    grounding?.live_doc ||
      facts ||
      route === "booking_tool" ||
      route === "severe_weather" ||
      route === "opening_hours"
  );
}

function formatRouteScore(route, value, origin) {
  if (route === "rag" && (origin?.source === "default_floor" || Number(value) === 0.5)) {
    return "default";
  }
  if (value == null || Number.isNaN(Number(value))) return "—";
  return Number(value).toFixed(2);
}

function StageIO({ request, payload, defaultOpen = true }) {
  if (!request && !payload) return null;
  const asText = (v) =>
    typeof v === "string" ? v : JSON.stringify(v, null, 2);
  return (
    <div className="bn-stage-io">
      {request != null && request !== "" ? (
        <details className="bn-llm-part" open={defaultOpen}>
          <summary>Request</summary>
          <pre className="bn-pre bn-pre-tall">{asText(request)}</pre>
        </details>
      ) : null}
      {payload != null && payload !== "" ? (
        <details className="bn-llm-part" open={defaultOpen}>
          <summary>Payload</summary>
          <pre className="bn-pre bn-pre-tall">{asText(payload)}</pre>
        </details>
      ) : null}
    </div>
  );
}

function turnsToPreview(turns) {
  return (turns || [])
    .map((t) => {
      const role = t.role || t.speaker || "";
      const content = t.content || t.message || "";
      const prefix =
        role === "user" || role === "parent" ? "Parent:" : "Bot:";
      return `${prefix} ${content}`;
    })
    .join("\n");
}

/** Strip the just-sent user (+ bot reply) so chat fallback = prior memory. */
function priorChatTurns(chatHistory, inbound) {
  let list = Array.isArray(chatHistory) ? [...chatHistory] : [];
  if (list.length && list[list.length - 1].role === "bot") {
    list = list.slice(0, -1);
  }
  if (
    list.length &&
    list[list.length - 1].role === "user" &&
    (!inbound || list[list.length - 1].message === inbound)
  ) {
    list = list.slice(0, -1);
  }
  return list;
}

function nodeAnchor(id, side = "bottom") {
  const n = LAYOUT[id];
  if (!n) return { x: 0, y: 0 };
  // Diamond ports sit on the diamond vertices (inset from the square bbox)
  if (id === "path") {
    const inset = n.w * 0.08;
    if (side === "top") return { x: n.x, y: n.y - n.h / 2 + inset };
    if (side === "bottom") return { x: n.x, y: n.y + n.h / 2 - inset };
    if (side === "left") return { x: n.x - n.w / 2 + inset, y: n.y };
    if (side === "right") return { x: n.x + n.w / 2 - inset, y: n.y };
    if (side === "sw") return { x: n.x - n.w * 0.28, y: n.y + n.h * 0.28 };
    if (side === "se") return { x: n.x + n.w * 0.28, y: n.y + n.h * 0.28 };
  }
  if (side === "top") return { x: n.x, y: n.y - n.h / 2 };
  if (side === "left") return { x: n.x - n.w / 2, y: n.y };
  if (side === "right") return { x: n.x + n.w / 2, y: n.y };
  if (side === "center") return { x: n.x, y: n.y };
  return { x: n.x, y: n.y + n.h / 2 };
}

function shorten(a, b, padStart = 2, padEnd = 10) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  return {
    start: {
      x: a.x + (dx / len) * padStart,
      y: a.y + (dy / len) * padStart,
    },
    end: {
      x: b.x - (dx / len) * padEnd,
      y: b.y - (dy / len) * padEnd,
    },
  };
}

function edgeGeometry(fromId, toId) {
  const a = LAYOUT[fromId];
  const b = LAYOUT[toId];
  if (!a || !b) return null;

  let rawStart;
  let rawEnd;

  if (fromId === "path" && toId === "fsm") {
    rawStart = nodeAnchor("path", "sw");
    rawEnd = nodeAnchor(toId, "top");
  } else if (fromId === "path" && toId === "rag") {
    rawStart = nodeAnchor("path", "se");
    rawEnd = nodeAnchor(toId, "top");
  } else if (fromId === "router" && toId === "arbiter") {
    rawStart = nodeAnchor("router", "right");
    rawEnd = nodeAnchor("arbiter", "left");
  } else if (fromId === "arbiter" && toId === "path") {
    rawStart = nodeAnchor("arbiter", "bottom");
    rawEnd = nodeAnchor("path", "right");
  } else if (fromId === "fsm" && toId === "llm") {
    rawStart = nodeAnchor("fsm", "right");
    rawEnd = nodeAnchor("llm", "left");
  } else if (Math.abs(b.x - a.x) > 40 && Math.abs(b.y - a.y) < 30) {
    rawStart = nodeAnchor(fromId, b.x < a.x ? "left" : "right");
    rawEnd = nodeAnchor(toId, b.x < a.x ? "right" : "left");
  } else {
    rawStart = nodeAnchor(fromId, "bottom");
    rawEnd = nodeAnchor(toId, "top");
  }

  const { start, end } = shorten(rawStart, rawEnd, 1, 11);
  const dx = Math.abs(end.x - start.x);
  const dy = Math.abs(end.y - start.y);
  let d;
  let labelAt;
  if (dx < 8) {
    // Vertical
    const midY = (start.y + end.y) / 2;
    d = `M ${start.x} ${start.y} L ${end.x} ${end.y}`;
    labelAt = { x: start.x + 10, y: midY };
  } else if (fromId === "arbiter" || toId === "arbiter") {
    const midY = (start.y + end.y) / 2;
    d = `M ${start.x} ${start.y} L ${start.x} ${midY} L ${end.x} ${midY} L ${end.x} ${end.y}`;
    labelAt = { x: (start.x + end.x) / 2, y: midY - 6 };
  } else {
    const midY = (start.y + end.y) / 2;
    d = `M ${start.x} ${start.y} C ${start.x} ${midY}, ${end.x} ${midY}, ${end.x} ${end.y}`;
    labelAt = { x: (start.x + end.x) / 2, y: midY - 5 };
  }
  return { d, labelAt, start, end };
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
      <div className="bn-match-summary-kicker">Match</div>
      <p className="bn-match-headline">{summary.headline}</p>
      <QueryHighlight
        segments={summary.segments}
        fallback={summary.query || summary.annotated}
        cue={summary.cue}
      />
      {summary.cue ? (
        <p className="bn-match-cue-line">
          Cue <code>«{summary.cue}»</code>
          {summary.route ? (
            <>
              {" "}
              → <code>{summary.route}</code>
            </>
          ) : null}
        </p>
      ) : null}
    </div>
  );
}

function LlmCallCard({ call, defaultOpen = false }) {
  const ms = call?.meta?.latency_ms;
  return (
    <details className="bn-llm-call" defaultOpen={defaultOpen}>
      <summary>
        <strong>{llmRoleLabel(call.role)}</strong>
        <span>
          {call.model || "—"}
          {ms != null ? ` · ${ms} ms` : ""}
          {call.response_chars != null ? ` · ${call.response_chars} out` : ""}
        </span>
      </summary>
      <div className="bn-llm-call-body">
        <details className="bn-llm-part">
          <summary>
            System prompt
            <em>{call.system_chars ?? (call.system || "").length} chars</em>
          </summary>
          <pre className="bn-pre bn-pre-tall">{call.system || "(empty)"}</pre>
        </details>
        <details className="bn-llm-part" open>
          <summary>
            User / context fed to the model
            <em>{call.user_chars ?? (call.user || "").length} chars</em>
          </summary>
          <pre className="bn-pre bn-pre-tall">{call.user || "(empty)"}</pre>
        </details>
        <details className="bn-llm-part" open>
          <summary>
            Model output
            <em>{call.response_chars ?? (call.response || "").length} chars</em>
          </summary>
          <pre className="bn-pre bn-pre-tall">{call.response || "(empty)"}</pre>
        </details>
      </div>
    </details>
  );
}

function RagHit({ hit, index }) {
  const [open, setOpen] = useState(false);
  const rank = hit.rank ?? index + 1;
  const body = hit.text || hit.snippet || "";
  const score = scoreLabel(hit.score);
  const why = hit.rerank?.why || null;
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
          {score && <code title="vector similarity (not sort key)">{score}</code>}
        </div>
        <span className="bn-hit-src">{hit.source}</span>
        {why ? <p className="bn-hit-snip bn-hit-why">Order: {why}</p> : null}
        {!open && body ? <p className="bn-hit-snip">{body}</p> : null}
        {hit.char_count != null ? (
          <div className="bn-hit-meta">
            {hit.char_count} chars · vector {score ?? "—"}
          </div>
        ) : null}
        <span className="bn-hit-expand-hint">
          {open ? "Collapse chunk" : "Expand full chunk"}
        </span>
      </button>
      {open && body ? <pre className="bn-hit-full">{body}</pre> : null}
    </li>
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
  const cue = evidence.cue || rule.matched_span?.text || "";
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
          {rule.score != null ? (
            <em>score {Number(rule.score).toFixed(2)}</em>
          ) : null}
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
          {rule.score_origin?.source && rule.score_origin.source !== "none" ? (
            <div className="bn-rule-block">
              <span>Score source</span>
              <p>
                <code>{rule.score_origin.source}</code>
                {rule.score_origin.score != null
                  ? ` = ${Number(rule.score_origin.score).toFixed(2)}`
                  : ""}
                {rule.score_origin.why ? ` — ${rule.score_origin.why}` : ""}
              </p>
            </div>
          ) : null}
          {checks.length > 0 ? (
            <ul className="bn-rule-checks">
              {checks.map((c, i) => (
                <li
                  key={`${rule.id}-chk-${i}`}
                  className={c.passed ? "pass" : "fail"}
                >
                  <strong>{c.passed ? "yes" : "no"}</strong>
                  <span>
                    {c.label || c.id}
                    {c.detail ? ` — ${c.detail}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
          {reason ? <p className="bn-note">{reason}</p> : null}
        </div>
      ) : null}
    </li>
  );
}

function Skipped({ children }) {
  return <p className="bn-empty">{children}</p>;
}

function NodeDetail({
  node,
  trace,
  answer,
  parentMessage = "",
  chatHistory = [],
  route,
  grounding,
  routing,
  llmCalls,
  history,
  rag,
  facts,
  hits,
  enrichMode,
  rulesEvaluated,
  winningRuleId,
  matchSummary,
  arbiter,
  path,
  liveOn,
  finalCheck,
  adminTodo,
  staffMessage,
  scoreLedger,
}) {
  if (!node) return null;
  if (!trace) {
    return (
      <p className="bn-empty">
        Send a parent message, then click a stage.
      </p>
    );
  }

  const inbound =
    trace.inbound_message ||
    parentMessage ||
    matchSummary?.query ||
    "";
  const priorTurns = history?.turns || [];
  const priorPreview =
    history?.prior_preview ||
    turnsToPreview(priorTurns) ||
    turnsToPreview(
      priorChatTurns(chatHistory, inbound).map((m) => ({
        role: m.role,
        content: m.message,
      }))
    );

  switch (node.id) {
    case "parent":
      return (
        <>
          <ul className="bn-fact-list">
            <li>
              <span>Session</span>
              <code>{trace.session_id || "—"}</code>
            </li>
          </ul>
          <pre className="bn-pre bn-pre-tall">{inbound || "(empty)"}</pre>
        </>
      );

    case "lang":
      return (
        <>
          <p className="bn-rules-hint">
            Detects <code>en</code> / <code>zh-HK</code> / <code>zh-CN</code>{" "}
            from the parent text (CJK ratio + traditional/simplified cues). That
            code filters Chroma retrieve to matching chunks; if none hit, retrieve
            retries without the language filter.
          </p>
          <ul className="bn-fact-list">
            <li>
              <span>Detected</span>
              <code>{trace.language || "—"}</code>
            </li>
            <li>
              <span>KB language filter</span>
              <code>{trace.language || "—"}</code>
            </li>
            <li>
              <span>Filter fallback</span>
              <code>
                {rag?.language_fallback
                  ? "retried without language filter"
                  : "not needed"}
              </code>
            </li>
          </ul>
          <StageIO
            request={inbound}
            payload={{
              language: trace.language || null,
              chroma_where: trace.language
                ? { language: trace.language }
                : null,
              language_fallback: Boolean(rag?.language_fallback),
            }}
          />
        </>
      );

    case "memory": {
      const loaded = priorTurns.length;
      return (
        <>
          <ul className="bn-fact-list">
            <li>
              <span>Prior turns</span>
              <code>
                {history?.turns_loaded ?? loaded}/{history?.limit ?? "—"}
              </code>
            </li>
            <li>
              <span>Enabled</span>
              <code>
                {history?.enabled == null
                  ? "—"
                  : history.enabled
                    ? "yes"
                    : "no"}
              </code>
            </li>
          </ul>
          <p className="bn-rules-hint">
            Prior turns only. Contextual retrieval rewrite (if any) is the{" "}
            <strong>Query rewrite</strong> node on the RAG arm.
          </p>
          <StageIO
            request={`session ${trace.session_id || "—"}`}
            payload={priorPreview || "(no prior turns)"}
          />
        </>
      );
    }

    case "rewrite": {
      const qr = rag?.query_rewrite || null;
      const grade = rag?.retrieval_grade || null;
      const ran = Boolean(
        hits.length || enrichMode || path === "rag" || qr || grade
      );
      if (!ran) {
        return <Skipped>Retrieve did not run — rewrite not evaluated.</Skipped>;
      }
      return (
        <>
          <p className="bn-rules-hint">
            {qr?.rule ||
              "CRAG-lite (Yan et al., 2024) + Self-RAG-style retry: retrieve → grade Correct/Ambiguous/Incorrect → only if not Correct and prior chat exists, cheap-rewrite once and re-retrieve (max 1)."}
          </p>
          <ul className="bn-fact-list">
            <li>
              <span>Protocol</span>
              <code>{qr?.protocol || "CRAG-lite"}</code>
            </li>
            <li>
              <span>Pass-1 grade</span>
              <code>
                {grade?.grade || qr?.grade || "—"}
                {grade?.why || qr?.grade_why
                  ? ` · ${grade?.why || qr?.grade_why}`
                  : ""}
              </code>
            </li>
            <li>
              <span>Retry</span>
              <code>
                {qr?.retried
                  ? `yes · kept ${qr?.kept || "pass2"}`
                  : qr?.reason || "no"}
              </code>
            </li>
            <li>
              <span>Rewritten</span>
              <code>{qr?.rewritten ? "yes" : "no"}</code>
            </li>
            <li>
              <span>Called LLM</span>
              <code>{qr?.called ? "yes" : "no"}</code>
            </li>
          </ul>
          <StageIO
            request={{
              parent: inbound,
              prior: priorPreview || "(no prior turns)",
              pass1_query: qr?.original || inbound,
            }}
            payload={{
              grade: grade || { grade: qr?.grade, why: qr?.grade_why },
              retrieval_query: rag?.retrieval_query || inbound,
              query_rewrite: qr || null,
            }}
          />
          {qr?.rewritten ? (
            <ul className="bn-fact-list">
              <li>
                <span>Pass 1</span>
                <code>{qr.original || inbound}</code>
              </li>
              <li>
                <span>Pass 2</span>
                <code>{qr.pass2_query || qr.query}</code>
              </li>
            </ul>
          ) : null}
          {llmCalls
            .filter((c) => c.role === "query_rewrite")
            .map((c, i) => (
              <LlmCallCard key={`qr-${i}`} call={c} defaultOpen />
            ))}
        </>
      );
    }

    case "router":
      return (
        <>
          <p className="bn-rules-hint">
            After scores (and optional arbiter): pick one route id. Path then
            maps that route to FSM vs RAG.
          </p>
          <ul className="bn-fact-list">
            <li>
              <span>Route</span>
              <code>{route || "—"}</code>
            </li>
            {routing?.hours_followup_promoted ? (
              <li>
                <span>Hours follow-up</span>
                <code>promoted</code>
              </li>
            ) : null}
            {routing?.dialogue_active ? (
              <li>
                <span>Booking chat open</span>
                <code>sticky</code>
              </li>
            ) : null}
            {routing?.cancel_active ? (
              <li>
                <span>Cancel chat open</span>
                <code>sticky</code>
              </li>
            ) : null}
          </ul>
          <StageIO
            request={{
              scores: routing?.scores || {},
              language: trace.language,
            }}
            payload={{
              route,
              sticky: {
                booking_open: Boolean(routing?.dialogue_active),
                cancel_open: Boolean(routing?.cancel_active),
              },
            }}
          />
        </>
      );

    case "rules":
      return (
        <>
          <div className="bn-score-howto">
            <p>
              <strong>Detector awards</strong> (not RAG similarity)
            </p>
            <ol>
              <li>Matching detectors award a fixed score.</li>
              <li>Drop scores under <code>0.28</code>.</li>
              <li>
                Highest wins; ties use priority (escalate → off-domain → weather
                → cancel → book → seats → hours → RAG).
              </li>
              <li>No match → RAG default path (no detector award).</li>
            </ol>
          </div>
          <MatchSummaryCard summary={matchSummary} />
          {routing?.scores && Object.keys(routing.scores).length > 0 ? (
            <details className="bn-rules" open>
              <summary>Scores</summary>
              <ul className="bn-fact-list">
                {Object.entries(routing.scores)
                  .sort((a, b) => Number(b[1]) - Number(a[1]))
                  .slice(0, 12)
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
                        <code>{formatRouteScore(k, v, origin)}</code>
                      </li>
                    );
                  })}
              </ul>
              {scoreLedger.length > 0 ? (
                <details className="bn-rules bn-ledger">
                  <summary>Ledger · {scoreLedger.length}</summary>
                  <ol className="bn-score-ledger">
                    {scoreLedger.map((e, i) => (
                      <li
                        key={`led-${i}`}
                        className={`bn-led-${e.op || "note"}`}
                      >
                        <strong>{e.op || "note"}</strong>
                        <span>
                          <code>{e.route}</code>
                          {e.score != null
                            ? ` = ${formatRouteScore(e.route, e.score, e)}`
                            : ""}
                          {e.source ? ` · ${e.source}` : ""}
                          {e.why ? ` — ${e.why}` : ""}
                        </span>
                      </li>
                    ))}
                  </ol>
                </details>
              ) : null}
            </details>
          ) : null}
          {rulesEvaluated.length > 0 ? (
            <ul className="bn-rule-list">
              {rulesEvaluated.map((r) => (
                <RuleEvidence
                  key={r.id}
                  rule={r}
                  isWinner={r.id === winningRuleId && Boolean(r.matched)}
                />
              ))}
            </ul>
          ) : (
            <Skipped>No rule trace on this turn.</Skipped>
          )}
        </>
      );

    case "arbiter": {
      const arbCalls = llmCalls.filter((c) => c.role === "intent_arbiter");
      if (!arbiter?.called) {
        return (
          <>
            <Skipped>
              Skipped
              {arbiter?.reason ? ` — ${arbiter.reason}` : ""}.
            </Skipped>
            {arbiter?.skip_detail ? (
              <p className="bn-note">{arbiter.skip_detail}</p>
            ) : null}
          </>
        );
      }
      return (
        <>
          <ul className="bn-fact-list">
            <li>
              <span>Decision</span>
              <code>{arbiter?.action || "—"}</code>
            </li>
            <li>
              <span>Model</span>
              <code>{arbiter?.model || "—"}</code>
            </li>
          </ul>
          {arbCalls.map((c, i) => (
            <LlmCallCard key={`arb-${i}`} call={c} defaultOpen />
          ))}
          {!arbCalls.length ? (
            <StageIO
              request={{ proposed: route, scores: routing?.scores }}
              payload={{
                action: arbiter?.action,
                reason: arbiter?.reason,
                raw: arbiter?.raw || null,
              }}
            />
          ) : null}
        </>
      );
    }

    case "path":
      return (
        <>
          <p className="bn-rules-hint">
            Branch from the route: <strong>FSM</strong> (tools / sticky
            dialogue) or <strong>RAG</strong> (retrieve → generate). Live feeds
            are a separate arm when used.
          </p>
          <ul className="bn-fact-list">
            <li>
              <span>Path</span>
              <code>{path === "rag" ? "RAG" : path === "fsm" ? "FSM" : "—"}</code>
            </li>
            <li>
              <span>Route</span>
              <code>{route || "—"}</code>
            </li>
            <li>
              <span>Live arm</span>
              <code>{liveOn ? "on" : "off"}</code>
            </li>
          </ul>
          <StageIO
            request={{ route, grounding: grounding?.mode }}
            payload={{
              path: path || null,
              live: Boolean(liveOn),
              why: grounding?.why || null,
            }}
          />
        </>
      );

    case "fsm":
      if (path !== "fsm") {
        return (
          <Skipped>
            FSM path not taken (this turn used RAG).
          </Skipped>
        );
      }
      return (
        <>
          <p className="bn-rules-hint">
            Tool / sticky dialogue for <code>{route || "—"}</code>.
          </p>
          <ul className="bn-fact-list">
            {trace?.booking?.stage ? (
              <li>
                <span>Booking</span>
                <code>{trace.booking.stage}</code>
              </li>
            ) : null}
            {trace?.cancel?.stage ? (
              <li>
                <span>Cancel</span>
                <code>{trace.cancel.stage}</code>
              </li>
            ) : null}
            {finalCheck?.action ? (
              <li>
                <span>Final check</span>
                <code>{finalCheck.action}</code>
              </li>
            ) : null}
            {grounding?.composed != null ? (
              <li>
                <span>Composed</span>
                <code>{grounding.composed ? "yes" : "no"}</code>
              </li>
            ) : null}
          </ul>
          <StageIO
            request={{ route, message: inbound }}
            payload={{
              booking: trace?.booking || null,
              cancel: trace?.cancel || null,
              final_check: finalCheck || null,
              grounding: grounding || null,
            }}
          />
          {llmCalls
            .filter(
              (c) =>
                String(c.role || "").startsWith("final_check") ||
                [
                  "booking_parse",
                  "cancel_pick",
                  "staff_email",
                  "grounded_compose",
                  "fact_adapt",
                  "query_rewrite",
                ].includes(c.role)
            )
            .map((c, i) => (
              <LlmCallCard
                key={`fsm-llm-${i}`}
                call={c}
                defaultOpen={i === 0}
              />
            ))}
        </>
      );

    case "rag":
      if (path !== "rag") {
        return (
          <Skipped>
            RAG path not taken
            {enrichMode ? " (FSM may still enrich via Chroma)." : "."}
          </Skipped>
        );
      }
      return (
        <>
          <p className="bn-rules-hint">
            Retrieve → generate. Chroma is asked for a pool, then we keep up to{" "}
            <code>top_k</code> after lexical re-rank (see Chroma KB). Generation
            still sees the parent’s original wording plus chat history.
          </p>
          <ul className="bn-fact-list">
            <li>
              <span>Hits kept</span>
              <code>
                {rag?.hit_count ?? hits.length}/{rag?.top_k ?? "—"}
              </code>
            </li>
            <li>
              <span>Rank #1 vector</span>
              <code>
                {scoreLabel(rag?.rank1_score ?? rag?.top_score ?? hits[0]?.score) ??
                  "—"}
              </code>
            </li>
            <li>
              <span>Best vector in set</span>
              <code>{scoreLabel(rag?.max_score) ?? "—"}</code>
            </li>
            <li>
              <span>Gap</span>
              <code>{rag?.gap_reason || "—"}</code>
            </li>
          </ul>
          <StageIO
            request={{
              parent: inbound,
              retrieval_query: rag?.retrieval_query || inbound,
              language: trace.language,
            }}
            payload={{
              hit_count: rag?.hit_count ?? hits.length,
              top_k: rag?.top_k ?? null,
              pool_k: rag?.pool_k ?? null,
              pool_returned: rag?.pool_returned ?? null,
              rank1_score: rag?.rank1_score ?? rag?.top_score ?? null,
              max_score: rag?.max_score ?? null,
              gap_reason: rag?.gap_reason || null,
              query_rewrite: rag?.query_rewrite || null,
            }}
          />
        </>
      );

    case "live":
      if (!liveOn) {
        return <Skipped>No live feed on this turn.</Skipped>;
      }
      return (
        <>
          <p className="bn-rules-hint">
            Live feed (HKO, hours engine, register seats, weather card). Privileged
            over KB when present.
          </p>
          {facts ? (
            <ul className="bn-fact-list">
              <li>
                <span>Resolved</span>
                <code>{facts.datetime || "—"}</code>
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
                <code>
                  {facts.is_holiday
                    ? facts.holiday_name || "yes"
                    : "no"}
                </code>
              </li>
              <li>
                <span>HKO</span>
                <code>
                  {facts.weather_hint
                    ? facts.weather_hint
                    : facts.parse_debug?.weather_applied
                      ? "checked · clear"
                      : "not run"}
                </code>
              </li>
            </ul>
          ) : null}
          <StageIO
            request={{ route, message: inbound }}
            payload={{
              live_doc: grounding?.live_doc
                ? `${grounding.live_doc_chars || "?"} chars`
                : false,
              facts: facts || null,
              opening_hours: trace?.opening_hours || null,
            }}
          />
        </>
      );

    case "chroma":
      if (!(hits.length || enrichMode || path === "rag")) {
        return <Skipped>KB retrieve did not run.</Skipped>;
      }
      if (!hits.length) {
        return <Skipped>Retrieve ran but no chunks returned.</Skipped>;
      }
      return (
        <>
          <p className="bn-rules-hint">
            {rag?.keep_rule ||
              `Ask Chroma for pool_k=${rag?.pool_k ?? "—"}, keep up to top_k=${
                rag?.top_k ?? "—"
              }. Fewer than top_k means the language filter / index had fewer matches — not a random subset.`}
          </p>
          <p className="bn-rules-hint">
            {rag?.order_rule ||
              "Order is lexical then vector score (age → keyword path → folder → token overlap → similarity). Highest score is not always #1."}
          </p>
          <div className="bn-rag-strip">
            <span>index {rag?.index_size ?? "—"}</span>
            <span>
              pool {rag?.pool_returned ?? "—"}/{rag?.pool_k ?? "—"}
            </span>
            <span>
              keep {rag?.hit_count ?? hits.length}/{rag?.top_k ?? "—"}
            </span>
            <span>
              #1 {scoreLabel(rag?.rank1_score ?? rag?.top_score ?? hits[0]?.score) ?? "—"}
            </span>
            <span>max {scoreLabel(rag?.max_score) ?? "—"}</span>
            <span>{rag?.embedding_mode || "—"}</span>
            {enrichMode ? <span>enrich</span> : null}
          </div>
          {rag?.retrieval_query &&
          rag.retrieval_query !== inbound ? (
            <ul className="bn-fact-list">
              <li>
                <span>Parent said</span>
                <code>{inbound}</code>
              </li>
              <li>
                <span>Retrieved with</span>
                <code>{rag.retrieval_query}</code>
              </li>
            </ul>
          ) : null}
          <StageIO
            request={{
              parent: inbound,
              retrieval_query: rag?.retrieval_query || inbound,
              language: trace.language,
              mode: enrichMode ? "grounded_enrich" : "retrieve",
              pool_k: rag?.pool_k ?? null,
              top_k: rag?.top_k ?? null,
            }}
            payload={hits.map((h) => ({
              rank: h.rank,
              source: h.source,
              score: h.score,
              title: h.title,
              order: h.rerank?.why || null,
            }))}
          />
          <ul className="bn-hits">
            {hits.map((h, i) => (
              <RagHit key={`${h.source}-${h.rank || i}`} hit={h} index={i} />
            ))}
          </ul>
        </>
      );

    case "llm": {
      const genCalls = llmCalls.filter(
        (c) =>
          c.role !== "intent_arbiter" &&
          c.role !== "query_rewrite" &&
          !String(c.role || "").startsWith("final_check")
      );
      return (
        <>
          <p className="bn-rules-hint">
            Main LLM calls via DashScope (Qwen). Corrective query rewrite lives
            on the Query rewrite node; arbiter / final-check on theirs.
          </p>
          {genCalls.length === 0 ? (
            <Skipped>No LLM call (template / deterministic reply).</Skipped>
          ) : (
            <div className="bn-llm-calls">
              {genCalls.map((call, i) => (
                <LlmCallCard
                  key={`${call.id || i}-${call.role}`}
                  call={call}
                  defaultOpen={i === genCalls.length - 1}
                />
              ))}
            </div>
          )}
        </>
      );
    }

    case "sheets": {
      const wrote =
        adminTodo ||
        staffMessage ||
        trace?.booking?.stage === "booked" ||
        trace?.cancel?.cancelled ||
        trace?.cancel?.stage === "cancelled";
      if (!wrote) {
        return <Skipped>No SheetPro / MailBox / CSV write.</Skipped>;
      }
      return (
        <StageIO
          request={{ route, session: trace.session_id }}
          payload={{
            admin_todo: adminTodo || null,
            staff_message: staffMessage || null,
            booking: trace?.booking?.booking || null,
            cancel: trace?.cancel || null,
          }}
        />
      );
    }

    case "answer":
      return (
        <>
          <p className="bn-rules-hint">
            What the parent sees after sanitize (gap tags stripped). Model I/O is
            on <em>LLM (DashScope)</em>.
          </p>
          <ul className="bn-fact-list">
            <li>
              <span>Provider</span>
              <code>{trace.provider || trace.answer_provider || "—"}</code>
            </li>
            <li>
              <span>Latency</span>
              <code>
                {trace.latency_ms != null ? `${trace.latency_ms} ms` : "—"}
              </code>
            </li>
          </ul>
          <pre className="bn-pre bn-pre-tall">{answer || "(empty)"}</pre>
        </>
      );

    default:
      return <Skipped>Unknown node.</Skipped>;
  }
}

function FlowNode({ node, lit, active }) {
  const pos = LAYOUT[node.id];
  const shape = node.shape;
  const stroke =
    shape === "cylinder"
      ? "rgba(70, 110, 85, 0.75)"
      : "rgba(18, 169, 188, 0.55)";
  const fill = "rgba(255,255,255,0.97)";

  return (
    <div
      role="button"
      tabIndex={0}
      data-node-id={node.id}
      className={`bn-flow-node bn-shape-${shape} bn-pipe-${node.kind} bn-pipe-${node.status}${
        lit ? " is-lit" : ""
      }${active ? " is-active" : ""}`}
      style={{
        left: pos.x,
        top: pos.y,
        width: pos.w,
        height: pos.h,
        marginLeft: -pos.w / 2,
        marginTop: -pos.h / 2,
      }}
      aria-haspopup="dialog"
      aria-expanded={active}
      title={`${node.label} · ${node.status} — click to inspect`}
    >
      {shape === "decision" ? (
        <svg
          className="bn-shape-svg"
          viewBox="0 0 100 100"
          aria-hidden="true"
        >
          <polygon
            points="50,3 97,50 50,97 3,50"
            fill={fill}
            stroke={stroke}
            strokeWidth="2.2"
          />
        </svg>
      ) : null}
      {shape === "cylinder" ? (
        <svg
          className="bn-shape-svg"
          viewBox="0 0 100 70"
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          <path
            d="M6 14 v40 a44 12 0 0 0 88 0 V14"
            fill={fill}
            stroke={stroke}
            strokeWidth="2.2"
          />
          <ellipse
            cx="50"
            cy="14"
            rx="44"
            ry="12"
            fill={fill}
            stroke={stroke}
            strokeWidth="2.2"
          />
        </svg>
      ) : null}
      <span className="bn-flow-node-body">
        <span className="bn-pipe-node-label">{node.label}</span>
        <span className="bn-pipe-node-status">{node.status}</span>
      </span>
    </div>
  );
}

export default function TurnPipeline({
  trace,
  answer = "",
  turnKey = "idle",
  chatHistory = [],
  parentMessage = "",
}) {
  const [activeId, setActiveId] = useState(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const viewportRef = useRef(null);
  const dragRef = useRef(null);
  const closeBtnRef = useRef(null);
  const fittedRef = useRef(false);
  const zoomRef = useRef(zoom);
  const panRef = useRef(pan);

  useEffect(() => {
    zoomRef.current = zoom;
  }, [zoom]);
  useEffect(() => {
    panRef.current = pan;
  }, [pan]);

  const route = trace?.route;
  const grounding = trace?.grounding;
  const routing = trace?.routing;
  const llmCalls = Array.isArray(trace?.llm_calls) ? trace.llm_calls : [];
  const history = trace?.history;
  const rag = trace?.rag;
  const facts = trace?.opening_hours?.facts;
  const enrichMode = rag?.mode === "grounded_enrich" || grounding?.used_rag;
  const hits = route === "rag" || enrichMode ? rag?.hits || [] : [];
  const rulesEvaluated =
    routing?.rules_evaluated || routing?.debug?.rules_evaluated || [];
  const winningRule = routing?.winning_rule || routing?.debug?.winning_rule || {};
  const matchSummary =
    routing?.match_summary || routing?.debug?.match_summary || {};
  const arbiter = routing?.debug?.arbiter || {};
  const winningRuleId = matchSummary?.rule_id || winningRule?.route || route;
  const scoreLedger =
    routing?.score_ledger ||
    routing?.debug?.score_ledger ||
    routing?.debug?.ledger ||
    [];
  const finalCheck =
    trace?.final_check ||
    trace?.debug?.final_check ||
    trace?.booking?.final_check ||
    trace?.cancel?.final_check ||
    null;
  const path = pathKind(route, grounding);
  const liveOn = liveUsed(route, grounding, facts);
  const adminTodo = trace?.admin_todo || trace?.debug?.admin_todo;
  const staffMessage = trace?.staff_message || trace?.debug?.staff_message;
  const inbound =
    trace?.inbound_message ||
    parentMessage ||
    matchSummary?.query ||
    "";

  const nodes = useMemo(() => {
    const hasTrace = Boolean(trace);
    const memLoaded = Number(history?.turns_loaded || 0) > 0;
    const genN = llmCalls.filter((c) => c.role !== "intent_arbiter").length;
    return [
      {
        id: "parent",
        label: "Parent message",
        kind: "start",
        shape: "terminal",
        status: hasTrace ? "done" : "idle",
      },
      {
        id: "lang",
        label: "Language detection",
        kind: "router",
        shape: "process",
        status: hasTrace ? "done" : "idle",
      },
      {
        id: "memory",
        label: "Session memory",
        kind: "store",
        shape: "cylinder",
        status: !hasTrace
          ? "idle"
          : history?.enabled === false
            ? "skipped"
            : memLoaded
              ? "done"
              : "empty",
      },
      {
        id: "rules",
        label: "Rules · scores",
        kind: "rules",
        shape: "process",
        status: !hasTrace
          ? "idle"
          : rulesEvaluated.length || routing?.scores
            ? "done"
            : "skipped",
      },
      {
        id: "arbiter",
        label: "Arbiter",
        kind: "llm",
        shape: "process",
        status: !hasTrace ? "idle" : arbiter?.called ? "done" : "skipped",
      },
      {
        id: "router",
        label: "Hybrid router",
        kind: "router",
        shape: "process",
        status: hasTrace ? "done" : "idle",
      },
      {
        id: "path",
        label: "FSM or RAG?",
        kind: "branch",
        shape: "decision",
        status: hasTrace && path ? "done" : "idle",
      },
      {
        id: "fsm",
        label: "FSM · tools",
        kind: "hard",
        shape: "process",
        status: !hasTrace ? "idle" : path === "fsm" ? "done" : "skipped",
      },
      {
        id: "rag",
        label: "RAG",
        kind: "rag",
        shape: "process",
        status: !hasTrace ? "idle" : path === "rag" ? "done" : "skipped",
      },
      {
        id: "live",
        label: "Live data",
        kind: "live",
        shape: "process",
        status: !hasTrace ? "idle" : liveOn ? "done" : "skipped",
      },
      {
        id: "rewrite",
        label: "Query rewrite",
        kind: "llm",
        shape: "process",
        status: !hasTrace
          ? "idle"
          : !(path === "rag" || enrichMode || hits.length)
            ? "skipped"
            : rag?.query_rewrite?.rewritten || rag?.query_rewrite?.retried
              ? "done"
              : rag?.retrieval_grade || rag?.query_rewrite
                ? "done"
                : "skipped",
      },
      {
        id: "chroma",
        label: "Chroma KB",
        kind: "store",
        shape: "cylinder",
        status: !hasTrace
          ? "idle"
          : hits.length || rag?.hit_count
            ? "done"
            : path === "rag" || enrichMode
              ? "empty"
              : "skipped",
      },
      {
        id: "llm",
        label: "LLM (DashScope)",
        kind: "llm",
        shape: "process",
        status: !hasTrace ? "idle" : genN ? "done" : "skipped",
      },
      {
        id: "sheets",
        label: "SheetPro · Mail",
        kind: "store",
        shape: "cylinder",
        status: !hasTrace
          ? "idle"
          : adminTodo ||
              staffMessage ||
              trace?.booking?.stage === "booked" ||
              trace?.cancel?.stage === "cancelled"
            ? "done"
            : "skipped",
      },
      {
        id: "answer",
        label: "Parent answer",
        kind: "end",
        shape: "terminal",
        status: hasTrace ? "done" : "idle",
      },
    ];
  }, [
    trace,
    route,
    history,
    rulesEvaluated.length,
    routing?.scores,
    arbiter,
    path,
    liveOn,
    facts,
    hits.length,
    rag?.hit_count,
    rag?.query_rewrite,
    rag?.retrieval_grade,
    enrichMode,
    llmCalls,
    adminTodo,
    staffMessage,
  ]);

  const statusById = useMemo(() => {
    const m = {};
    for (const n of nodes) m[n.id] = n.status;
    return m;
  }, [nodes]);

  const activePath = useMemo(() => {
    if (!trace) return new Set();
    const lit = new Set([
      "parent",
      "lang",
      "memory",
      "rules",
      "router",
      "path",
      "answer",
    ]);
    if (history?.enabled === false) lit.delete("memory");
    if (arbiter?.called) lit.add("arbiter");
    if (path === "fsm") {
      lit.add("fsm");
      if (liveOn) lit.add("live");
      if (hits.length || enrichMode) {
        lit.add("rewrite");
        lit.add("chroma");
      }
      if (
        adminTodo ||
        staffMessage ||
        trace?.booking?.stage === "booked" ||
        trace?.cancel?.stage === "cancelled"
      ) {
        lit.add("sheets");
      }
    } else if (path === "rag") {
      lit.add("rag");
      lit.add("rewrite");
      lit.add("chroma");
      if (liveOn) lit.add("live");
      if (adminTodo) lit.add("sheets");
    }
    if (llmCalls.some((c) => c.role !== "intent_arbiter")) lit.add("llm");
    return lit;
  }, [
    trace,
    history?.enabled,
    arbiter,
    path,
    liveOn,
    hits.length,
    enrichMode,
    llmCalls,
    adminTodo,
    staffMessage,
  ]);

  const litEdgeIds = useMemo(() => {
    if (!trace) return new Set();
    const lit = new Set();
    const on = (id) => activePath.has(id);
    const ok = (id) =>
      statusById[id] === "done" ||
      statusById[id] === "empty" ||
      (id === "memory" && on(id)) ||
      (id === "rewrite" && on(id));
    const link = (from, to, edgeId) => {
      if (on(from) && on(to) && ok(from) && ok(to)) lit.add(edgeId);
    };
    link("parent", "lang", "e-parent-lang");
    link("lang", "memory", "e-lang-mem");
    link("memory", "rules", "e-mem-rules");
    link("rules", "router", "e-rules-router");
    if (arbiter?.called) {
      link("router", "arbiter", "e-router-arb");
      link("arbiter", "path", "e-arb-path");
    } else {
      link("router", "path", "e-router-path");
    }
    if (path === "fsm") link("path", "fsm", "e-path-fsm");
    if (path === "rag") link("path", "rag", "e-path-rag");
    if (on("fsm") && on("live")) link("fsm", "live", "e-fsm-live");
    if (on("fsm") && on("llm")) link("fsm", "llm", "e-fsm-llm");
    if (on("fsm") && on("sheets")) link("fsm", "sheets", "e-fsm-sheets");
    if (on("rag") && on("rewrite")) link("rag", "rewrite", "e-rag-rewrite");
    if (on("rewrite") && on("chroma")) {
      link("rewrite", "chroma", "e-rewrite-chroma");
    }
    if (on("chroma") && on("llm")) link("chroma", "llm", "e-chroma-llm");
    if (on("live") && on("llm")) link("live", "llm", "e-live-llm");
    if (on("live") && on("rewrite") && enrichMode) {
      link("live", "rewrite", "e-live-rewrite");
    }
    if (on("llm")) link("llm", "answer", "e-llm-answer");
    if (on("sheets")) link("sheets", "answer", "e-sheets-answer");
    return lit;
  }, [trace, activePath, statusById, arbiter, path, enrichMode]);

  const edgeLabels = useMemo(() => {
    // Only scores / decisions — not narrative labels on every hop.
    const winScore =
      routing?.scores?.[winningRuleId] ?? routing?.scores?.[route];
    const winOrigin =
      routing?.score_origins?.[winningRuleId] ||
      routing?.score_origins?.[route];
    const pick =
      winningRuleId || route
        ? `${winningRuleId || route}${
            winScore != null
              ? ` ${formatRouteScore(
                  winningRuleId || route,
                  winScore,
                  winOrigin
                )}`
              : ""
          }`
        : "";
    const labels = {
      "e-rules-router": pick ? `→ ${pick}` : "",
      "e-arb-path": arbiter?.called ? arbiter.action || "keep" : "",
      "e-path-fsm": path === "fsm" ? "FSM" : "",
      "e-path-rag": path === "rag" ? "RAG" : "",
      "e-rag-rewrite": rag?.retrieval_grade?.grade
        ? String(rag.retrieval_grade.grade)
        : "",
      "e-rewrite-chroma": rag?.query_rewrite?.retried
        ? "retry ×1"
        : rag?.query_rewrite?.reason === "retrieval_ok"
          ? "pass-1 ok"
          : "",
      "e-chroma-llm":
        hits.length && scoreLabel(hits[0]?.score)
          ? `sim ${scoreLabel(hits[0]?.score)}`
          : "",
    };
    return labels;
  }, [routing, winningRuleId, route, arbiter, path, hits, rag]);

  const fitView = useCallback(() => {
    const el = viewportRef.current;
    if (!el) return false;
    // Skip while the system pane is display:none (staff desk) — avoids zoom flash.
    if (el.offsetParent === null && el.getClientRects().length === 0) return false;
    const { width, height } = el.getBoundingClientRect();
    if (width < 120 || height < 120) return false;
    const gw = WORLD.maxX - WORLD.minX;
    const gh = WORLD.maxY - WORLD.minY;
    const pad = 36;
    const nextZoom = Math.min(
      MAX_ZOOM,
      Math.max(MIN_ZOOM, Math.min((width - pad) / gw, (height - pad) / gh) * 0.98)
    );
    const cx = (WORLD.minX + WORLD.maxX) / 2;
    const cy = (WORLD.minY + WORLD.maxY) / 2;
    const nextPan = {
      x: width / 2 - cx * nextZoom,
      y: height / 2 - cy * nextZoom,
    };
    zoomRef.current = nextZoom;
    panRef.current = nextPan;
    setZoom(nextZoom);
    setPan(nextPan);
    return true;
  }, []);

  useEffect(() => {
    setActiveId(null);
  }, [turnKey]);

  // Fit once when the canvas has a real size; never on hide/show of staff desk.
  useEffect(() => {
    if (fittedRef.current) return undefined;
    let tries = 0;
    const tick = () => {
      tries += 1;
      if (fitView()) {
        fittedRef.current = true;
        return;
      }
      if (tries < 20) window.setTimeout(tick, 50);
    };
    const t = window.setTimeout(tick, 30);
    return () => window.clearTimeout(t);
  }, [fitView]);

  useEffect(() => {
    let timer = null;
    const onResize = () => {
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        const el = viewportRef.current;
        if (!el) return;
        if (el.offsetParent === null && el.getClientRects().length === 0) return;
        fitView();
      }, 120);
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      if (timer) window.clearTimeout(timer);
    };
  }, [fitView]);

  useEffect(() => {
    if (!activeId) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") setActiveId(null);
    };
    window.addEventListener("keydown", onKey);
    const t = window.setTimeout(() => closeBtnRef.current?.focus(), 40);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.clearTimeout(t);
    };
  }, [activeId]);

  // Mouse wheel / trackpad zoom (non-passive so we can prevent page scroll)
  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return undefined;
    const onWheel = (e) => {
      e.preventDefault();
      e.stopPropagation();
      const rect = el.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      const z = zoomRef.current;
      const p = panRef.current;
      let dy = e.deltaY;
      if (e.deltaMode === 1) dy *= 16;
      if (e.deltaMode === 2) dy *= rect.height;
      const factor = Math.exp(-dy * 0.0018);
      const next = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z * factor));
      const wx = (mx - p.x) / z;
      const wy = (my - p.y) / z;
      const nextPan = { x: mx - wx * next, y: my - wy * next };
      zoomRef.current = next;
      panRef.current = nextPan;
      setZoom(next);
      setPan(nextPan);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const onPointerDown = (e) => {
    if (e.button !== 0 && e.button !== 1) return;
    if (e.target.closest(".bn-flow-controls")) return;
    const nodeEl = e.target.closest?.("[data-node-id]");
    dragRef.current = {
      x: e.clientX,
      y: e.clientY,
      panX: panRef.current.x,
      panY: panRef.current.y,
      moved: false,
      nodeId: nodeEl?.getAttribute("data-node-id") || null,
    };
    e.currentTarget.setPointerCapture?.(e.pointerId);
  };

  const onPointerMove = (e) => {
    const d = dragRef.current;
    if (!d) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    if (Math.abs(dx) + Math.abs(dy) > 4) d.moved = true;
    if (!d.moved) return;
    const next = { x: d.panX + dx, y: d.panY + dy };
    panRef.current = next;
    setPan(next);
  };

  const onPointerUp = () => {
    const d = dragRef.current;
    dragRef.current = null;
    if (d && !d.moved && d.nodeId) setActiveId(d.nodeId);
  };

  const active = nodes.find((n) => n.id === activeId) || null;

  const metaLine = trace
    ? `${route || "—"} · ${
        trace.latency_ms != null ? `${trace.latency_ms} ms` : "—"
      }${trace.language ? ` · ${trace.language}` : ""}${
        liveOn ? " · live" : ""
      }`
    : "Drag to pan · scroll to zoom · click a node";

  return (
    <div className="bn-pipeline bn-pipeline--canvas">
      <header className="bn-pipeline-head bn-pipeline-head--float">
        <div>
          <strong>Request flow</strong>
          <span>{metaLine}</span>
        </div>
        <div
          className="bn-flow-controls"
          role="toolbar"
          aria-label="Canvas controls"
        >
          <button
            type="button"
            onClick={() => {
              const z = zoomRef.current;
              const next = Math.min(MAX_ZOOM, z * 1.12);
              zoomRef.current = next;
              setZoom(next);
            }}
            title="Zoom in"
            aria-label="Zoom in"
          >
            +
          </button>
          <button
            type="button"
            onClick={() => {
              const z = zoomRef.current;
              const next = Math.max(MIN_ZOOM, z / 1.12);
              zoomRef.current = next;
              setZoom(next);
            }}
            title="Zoom out"
            aria-label="Zoom out"
          >
            −
          </button>
          <button
            type="button"
            className="bn-flow-fit"
            onClick={fitView}
            title="Fit to view"
            aria-label="Fit to view"
          >
            Fit
          </button>
        </div>
      </header>

      <div
        ref={viewportRef}
        className="bn-flow-viewport"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        <div
          className="bn-flow-world"
          style={{
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
          }}
        >
          <svg
            className="bn-flow-edges"
            width={WORLD.maxX - WORLD.minX}
            height={WORLD.maxY - WORLD.minY}
            viewBox={`${WORLD.minX} ${WORLD.minY} ${WORLD.maxX - WORLD.minX} ${
              WORLD.maxY - WORLD.minY
            }`}
            style={{ left: WORLD.minX, top: WORLD.minY }}
            aria-hidden="true"
          >
            <defs>
              <marker
                id="bn-flow-arrow"
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="6"
                markerHeight="6"
                orient="auto-start-reverse"
                markerUnits="strokeWidth"
              >
                <path d="M 0 1.2 L 9 5 L 0 8.8 Z" fill="rgba(18,169,188,0.35)" />
              </marker>
              <marker
                id="bn-flow-arrow-lit"
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="6"
                markerHeight="6"
                orient="auto-start-reverse"
                markerUnits="strokeWidth"
              >
                <path d="M 0 1.2 L 9 5 L 0 8.8 Z" fill="#0b7f8f" />
              </marker>
            </defs>
            {EDGES.map((edge) => {
              const geo = edgeGeometry(edge.from, edge.to);
              if (!geo) return null;
              const lit = litEdgeIds.has(edge.id);
              const label =
                (lit && edgeLabels[edge.id]) || (lit ? edge.label : "") || "";
              return (
                <g key={edge.id}>
                  <path
                    d={geo.d}
                    className={`bn-pipe-edge${lit ? " is-lit" : ""}${
                      edge.dashed ? " is-dashed" : ""
                    }`}
                    markerEnd={lit ? "url(#bn-flow-arrow-lit)" : "url(#bn-flow-arrow)"}
                  />
                  {label ? (
                    <text
                      x={geo.labelAt.x}
                      y={geo.labelAt.y}
                      className={`bn-pipe-edge-label${lit ? " is-lit" : ""}`}
                      textAnchor="middle"
                    >
                      {label}
                    </text>
                  ) : null}
                </g>
              );
            })}
          </svg>

          <div
            className="bn-flow-nodes"
            role="list"
            aria-label="System architecture"
          >
            {nodes.map((n) => (
              <FlowNode
                key={n.id}
                node={n}
                lit={
                  activePath.has(n.id) &&
                  (n.status === "done" || n.status === "empty")
                }
                active={activeId === n.id}
              />
            ))}
          </div>
        </div>
      </div>

      {active && typeof document !== "undefined"
        ? createPortal(
            <div
              className="bn-pipe-modal-root"
              role="presentation"
              onMouseDown={(e) => {
                if (e.target === e.currentTarget) setActiveId(null);
              }}
            >
              <div
                className="bn-pipe-modal"
                role="dialog"
                aria-modal="true"
                aria-labelledby="bn-pipe-modal-title"
              >
                <header className="bn-pipe-modal-head">
                  <div>
                    <h3 id="bn-pipe-modal-title">{active.label}</h3>
                    <span className={`bn-pipe-pill bn-pipe-${active.status}`}>
                      {active.status}
                    </span>
                  </div>
                  <button
                    ref={closeBtnRef}
                    type="button"
                    className="bn-pipe-modal-close"
                    aria-label="Close"
                    onClick={() => setActiveId(null)}
                  >
                    ×
                  </button>
                </header>
                <div className="bn-pipe-modal-body">
                  <NodeDetail
                    node={active}
                    trace={trace}
                    answer={answer}
                    parentMessage={inbound}
                    chatHistory={chatHistory}
                    route={route}
                    grounding={grounding}
                    routing={routing}
                    llmCalls={llmCalls}
                    history={history}
                    rag={rag}
                    facts={facts}
                    hits={hits}
                    enrichMode={enrichMode}
                    rulesEvaluated={rulesEvaluated}
                    winningRuleId={winningRuleId}
                    matchSummary={matchSummary}
                    arbiter={arbiter}
                    path={path}
                    liveOn={liveOn}
                    finalCheck={finalCheck}
                    adminTodo={adminTodo}
                    staffMessage={staffMessage}
                    scoreLedger={scoreLedger}
                  />
                </div>
              </div>
            </div>,
            document.body
          )
        : null}
    </div>
  );
}
