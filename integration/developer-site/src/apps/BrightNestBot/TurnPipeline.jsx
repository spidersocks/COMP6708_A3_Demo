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
  action_policy: "Action policy (cheap)",
  commit_audit: "Commit audit (cheap)",
  grounded_compose: "Grounded compose (main)",
  fact_adapt: "Fact rewrite (main)",
  rag_generate: "RAG generate (main)",
  gap_polish: "Gap polish (cheap)",
  cancel_pick: "Cancel pick (cheap)",
  booking_parse: "Booking DST (cheap JSON)",
  booking_dst: "Booking DST (cheap JSON)",
  cancel_dst: "Cancel DST (cheap JSON)",
  query_rewrite: "Query rewrite (cheap)",
  staff_email: "Staff email draft (cheap)",
  main_generate: "Main generate",
  cheap_llm: "Cheap LLM",
  cheap_json: "Cheap JSON",
};

/** Parent-facing main generate — the DashScope LLM node only. */
const MAIN_LLM_ROLES = new Set([
  "rag_generate",
  "grounded_compose",
  "fact_adapt",
  "main_generate",
  "gap_polish",
]);

/** Slot-DST / pick JSON — shown on the DST node, never the final LLM node. */
const DST_LLM_ROLES = new Set([
  "booking_dst",
  "booking_parse",
  "cancel_dst",
  "cancel_pick",
]);

function isFinalCheckCall(call) {
  return String(call?.role || "").startsWith("final_check");
}

function isStaffEmailCall(call) {
  return String(call?.role || "") === "staff_email";
}

function isMainLlmCall(call) {
  const role = String(call?.role || "");
  if (MAIN_LLM_ROLES.has(role)) return true;
  if (DST_LLM_ROLES.has(role)) return false;
  if (isFinalCheckCall(call) || isStaffEmailCall(call)) return false;
  if (role === "intent_arbiter" || role === "query_rewrite") return false;
  if (role === "action_policy" || role === "commit_audit") return false;
  // Unknown roles: only treat as main if they look like free-text generate
  return Boolean(call?.response) && !String(call?.response || "").trim().startsWith("{");
}

/**
 * Compact top-down layout (world px, origin at spine).
 * Spine: parent → language → memory → rules → router (FSM|RAG) → …
 * Scores before the route pick; live data is a side arm.
 */
const LAYOUT = {
  parent: { x: 0, y: 0, w: 156, h: 42 },
  lang: { x: 0, y: 72, w: 148, h: 42 },
  memory: { x: 0, y: 150, w: 148, h: 72 },
  rules: { x: 0, y: 250, w: 146, h: 46 },
  // Combined hybrid router + FSM|RAG branch (was two nodes)
  router: { x: 0, y: 380, w: 128, h: 128 },
  arbiter: { x: 210, y: 380, w: 120, h: 46 },
  fsm: { x: -280, y: 560, w: 140, h: 46 },
  rag: { x: 280, y: 560, w: 140, h: 46 },
  dst: { x: -280, y: 650, w: 168, h: 46 },
  // Commit gate + staff inbox draft — own nodes (not squashed into FSM)
  gate: { x: -280, y: 740, w: 168, h: 46 },
  staffmail: { x: -280, y: 830, w: 168, h: 46 },
  live: { x: -280, y: 920, w: 140, h: 46 },
  // Hybrid retrieve — further right so FSM→LLM corridor stays clear
  dense: { x: 200, y: 680, w: 136, h: 76 },
  sparse: { x: 400, y: 680, w: 136, h: 76 },
  fuse: { x: 300, y: 800, w: 148, h: 48 },
  chroma: { x: 300, y: 910, w: 148, h: 76 },
  rewrite: { x: 300, y: 1020, w: 152, h: 48 },
  chroma2: { x: 460, y: 1140, w: 148, h: 76 },
  sheets: { x: -280, y: 1140, w: 168, h: 76 },
  llm: { x: -40, y: 1140, w: 160, h: 48 },
  answer: { x: 0, y: 1260, w: 156, h: 42 },
};

/** Left corridor for FSM/DST/Live → LLM (between sheets and RAG arm). */
const FSM_LLM_X = -140;

const EDGES = [
  { id: "e-parent-lang", from: "parent", to: "lang" },
  { id: "e-lang-mem", from: "lang", to: "memory" },
  { id: "e-mem-rules", from: "memory", to: "rules" },
  { id: "e-rules-router", from: "rules", to: "router" },
  { id: "e-router-arb", from: "router", to: "arbiter", dashed: true },
  { id: "e-arb-router", from: "arbiter", to: "router", dashed: true },
  { id: "e-router-fsm", from: "router", to: "fsm" },
  { id: "e-router-rag", from: "router", to: "rag" },
  { id: "e-fsm-dst", from: "fsm", to: "dst" },
  { id: "e-fsm-gate", from: "fsm", to: "gate", dashed: true },
  { id: "e-dst-gate", from: "dst", to: "gate", dashed: true },
  { id: "e-gate-staffmail", from: "gate", to: "staffmail" },
  { id: "e-staffmail-sheets", from: "staffmail", to: "sheets" },
  { id: "e-gate-sheets", from: "gate", to: "sheets", dashed: true },
  { id: "e-gate-answer", from: "gate", to: "answer", dashed: true },
  { id: "e-dst-live", from: "dst", to: "live", dashed: true },
  { id: "e-fsm-live", from: "fsm", to: "live", dashed: true },
  { id: "e-dst-llm", from: "dst", to: "llm", dashed: true },
  { id: "e-dst-answer", from: "dst", to: "answer", dashed: true },
  { id: "e-fsm-llm", from: "fsm", to: "llm" },
  { id: "e-fsm-answer", from: "fsm", to: "answer", dashed: true },
  { id: "e-fsm-sheets", from: "fsm", to: "sheets", dashed: true },
  { id: "e-rag-dense", from: "rag", to: "dense" },
  { id: "e-rag-sparse", from: "rag", to: "sparse" },
  { id: "e-dense-fuse", from: "dense", to: "fuse" },
  { id: "e-sparse-fuse", from: "sparse", to: "fuse" },
  { id: "e-fuse-chroma", from: "fuse", to: "chroma" },
  { id: "e-chroma-rewrite", from: "chroma", to: "rewrite" },
  { id: "e-rewrite-chroma2", from: "rewrite", to: "chroma2", dashed: true },
  { id: "e-chroma2-llm", from: "chroma2", to: "llm", dashed: true },
  { id: "e-rewrite-llm", from: "rewrite", to: "llm" },
  { id: "e-live-llm", from: "live", to: "llm", dashed: true },
  { id: "e-live-fuse", from: "live", to: "fuse", dashed: true },
  { id: "e-llm-answer", from: "llm", to: "answer" },
  { id: "e-sheets-answer", from: "sheets", to: "answer" },
];

const WORLD = { minX: -450, minY: -28, maxX: 560, maxY: 1340 };
/** Default Fit: top of the spine (parent → router), not the whole flowchart. */
const FIT_FOCUS = { minX: -220, minY: -24, maxX: 260, maxY: 520 };
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 2.0;
const PAN_MARGIN = 72;

function clampPan(pan, zoom, vw, vh, margin = PAN_MARGIN) {
  const L = WORLD.minX * zoom;
  const R = WORLD.maxX * zoom;
  const T = WORLD.minY * zoom;
  const B = WORLD.maxY * zoom;
  let minX = margin - R;
  let maxX = vw - margin - L;
  let minY = margin - B;
  let maxY = vh - margin - T;
  if (minX > maxX) {
    const mid = ((WORLD.minX + WORLD.maxX) / 2) * zoom;
    minX = maxX = vw / 2 - mid;
  }
  if (minY > maxY) {
    const mid = ((WORLD.minY + WORLD.maxY) / 2) * zoom;
    minY = maxY = vh / 2 - mid;
  }
  return {
    x: Math.min(maxX, Math.max(minX, pan.x)),
    y: Math.min(maxY, Math.max(minY, pan.y)),
  };
}

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

/** Label the channel score on a packed hit (dense cosine or BM25). */
function channelScoreKind(hit) {
  if (!hit) return null;
  const kind =
    hit.score_kind ||
    hit.rerank?.score_kind ||
    (hit.fusion?.channels === "sparse" || hit.channel === "sparse"
      ? "BM25"
      : hit.fusion?.channels || hit.channel
        ? "dense"
        : null);
  return kind;
}

function labeledChannelScore(hit) {
  const score = scoreLabel(hit?.score);
  if (!score) return null;
  const kind = channelScoreKind(hit) || "score";
  return `${kind} ${score}`;
}

/** Edge / summary: pack slot + labeled channel score. */
function rankedPassLabel(hit) {
  if (!hit) return null;
  const parts = [];
  const slot = hit.pack?.slot || null;
  if (slot) parts.push(String(slot).replace(/_/g, " "));
  const ch = labeledChannelScore(hit);
  if (ch) parts.push(ch);
  return parts.length ? parts.join(" · ") : null;
}

function isChannelPack(hy, rag) {
  const mode = hy?.mode || rag?.retrieve_mode;
  return mode === "channel_pack" || mode === "hybrid_rrf";
}

function clip(text, n = 120) {
  const s = (text || "").trim();
  if (s.length <= n) return s;
  return `${s.slice(0, n - 1)}…`;
}

/** FSM (tools / sticky dialogue) vs RAG (retrieve → generate). */
function pathKind(route, grounding) {
  const g = grounding?.mode;
  // "pure_rag" kept for older traces; new API emits "rag"
  if (g === "rag" || g === "pure_rag" || route === "rag") return "rag";
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
  if (id === "router") {
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
  if (side === "sw") return { x: n.x - n.w * 0.32, y: n.y + n.h / 2 };
  if (side === "se") return { x: n.x + n.w * 0.32, y: n.y + n.h / 2 };
  if (side === "center") return { x: n.x, y: n.y };
  return { x: n.x, y: n.y + n.h / 2 };
}

function shorten(a, b, padStart = 2, padEnd = 14) {
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

/** Shorten only the end of a polyline so the arrowhead sits on the last segment. */
function shortenPolyEnd(pts, padEnd = 14) {
  if (!pts || pts.length < 2) return pts || [];
  const out = pts.map((p) => ({ ...p }));
  const a = out[out.length - 2];
  const b = out[out.length - 1];
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  const cut = Math.min(padEnd, Math.max(0, len - 2));
  out[out.length - 1] = {
    x: b.x - (dx / len) * cut,
    y: b.y - (dy / len) * cut,
  };
  return out;
}

function edgeGeometry(fromId, toId) {
  const a = LAYOUT[fromId];
  const b = LAYOUT[toId];
  if (!a || !b) return null;

  let rawStart;
  let rawEnd;
  let elbow = null; // optional mid waypoint(s)

  if (fromId === "router" && toId === "fsm") {
    rawStart = nodeAnchor("router", "sw");
    rawEnd = nodeAnchor(toId, "top");
  } else if (fromId === "router" && toId === "rag") {
    rawStart = nodeAnchor("router", "se");
    rawEnd = nodeAnchor(toId, "top");
  } else if (fromId === "router" && toId === "arbiter") {
    rawStart = nodeAnchor("router", "right");
    rawEnd = nodeAnchor("arbiter", "left");
  } else if (fromId === "arbiter" && toId === "router") {
    rawStart = nodeAnchor("arbiter", "bottom");
    rawEnd = nodeAnchor("router", "right");
  } else if (fromId === "fsm" && toId === "dst") {
    rawStart = nodeAnchor("fsm", "bottom");
    rawEnd = nodeAnchor("dst", "top");
  } else if (fromId === "fsm" && toId === "gate") {
    rawStart = nodeAnchor("fsm", "bottom");
    rawEnd = nodeAnchor("gate", "top");
  } else if (fromId === "dst" && toId === "gate") {
    rawStart = nodeAnchor("dst", "bottom");
    rawEnd = nodeAnchor("gate", "top");
  } else if (fromId === "gate" && toId === "staffmail") {
    rawStart = nodeAnchor("gate", "bottom");
    rawEnd = nodeAnchor("staffmail", "top");
  } else if (fromId === "staffmail" && toId === "sheets") {
    rawStart = nodeAnchor("staffmail", "bottom");
    rawEnd = nodeAnchor("sheets", "top");
  } else if (fromId === "gate" && toId === "sheets") {
    rawStart = nodeAnchor("gate", "bottom");
    rawEnd = nodeAnchor("sheets", "top");
  } else if (fromId === "gate" && toId === "answer") {
    rawStart = nodeAnchor("gate", "right");
    rawEnd = nodeAnchor("answer", "left");
    elbow = [
      { x: FSM_LLM_X, y: a.y },
      { x: FSM_LLM_X, y: b.y },
    ];
  } else if (
    (fromId === "fsm" || fromId === "dst" || fromId === "live") &&
    toId === "llm"
  ) {
    // Left corridor — clear of dense/chroma on the right and sheets on the far left
    rawStart = nodeAnchor(fromId, "right");
    rawEnd = nodeAnchor("llm", "left");
    elbow = [
      { x: FSM_LLM_X, y: a.y },
      { x: FSM_LLM_X, y: b.y },
    ];
  } else if (
    (fromId === "fsm" || fromId === "dst") &&
    toId === "answer"
  ) {
    // Template booking/cancel: DST/FSM → parent answer (no DashScope generate)
    rawStart = nodeAnchor(fromId, "right");
    rawEnd = nodeAnchor("answer", "left");
    elbow = [
      { x: FSM_LLM_X, y: a.y },
      { x: FSM_LLM_X, y: b.y },
    ];
  } else if (fromId === "fsm" && toId === "sheets") {
    rawStart = nodeAnchor("fsm", "bottom");
    rawEnd = nodeAnchor("sheets", "top");
  } else if (fromId === "dst" && toId === "live") {
    rawStart = nodeAnchor("dst", "bottom");
    rawEnd = nodeAnchor("live", "top");
  } else if (fromId === "rag" && toId === "dense") {
    rawStart = nodeAnchor("rag", "sw");
    rawEnd = nodeAnchor("dense", "top");
  } else if (fromId === "rag" && toId === "sparse") {
    rawStart = nodeAnchor("rag", "se");
    rawEnd = nodeAnchor("sparse", "top");
  } else if (fromId === "dense" && toId === "fuse") {
    rawStart = nodeAnchor("dense", "bottom");
    rawEnd = nodeAnchor("fuse", "left");
  } else if (fromId === "sparse" && toId === "fuse") {
    rawStart = nodeAnchor("sparse", "bottom");
    rawEnd = nodeAnchor("fuse", "right");
  } else if (fromId === "rewrite" && toId === "llm") {
    // Regular curve — pass 2 is further right
    rawStart = nodeAnchor("rewrite", "bottom");
    rawEnd = nodeAnchor("llm", "top");
  } else if (fromId === "rewrite" && toId === "chroma2") {
    rawStart = nodeAnchor("rewrite", "right");
    rawEnd = nodeAnchor("chroma2", "top");
  } else if (fromId === "chroma2" && toId === "llm") {
    rawStart = nodeAnchor("chroma2", "left");
    rawEnd = nodeAnchor("llm", "right");
  } else if (fromId === "live" && toId === "fuse") {
    rawStart = nodeAnchor("live", "right");
    rawEnd = nodeAnchor("fuse", "left");
  } else if (Math.abs(b.x - a.x) > 40 && Math.abs(b.y - a.y) < 30) {
    rawStart = nodeAnchor(fromId, b.x < a.x ? "left" : "right");
    rawEnd = nodeAnchor(toId, b.x < a.x ? "right" : "left");
  } else {
    rawStart = nodeAnchor(fromId, "bottom");
    rawEnd = nodeAnchor(toId, "top");
  }

  let d;
  let labelAt;
  let start;
  let end;
  if (elbow && elbow.length) {
    const pts = shortenPolyEnd([rawStart, ...elbow, rawEnd], 14);
    start = pts[0];
    end = pts[pts.length - 1];
    d = `M ${pts[0].x} ${pts[0].y}` + pts.slice(1).map((p) => ` L ${p.x} ${p.y}`).join("");
    const mid = pts[Math.floor(pts.length / 2)];
    labelAt = { x: mid.x + 8, y: mid.y - 6 };
  } else {
    ({ start, end } = shorten(rawStart, rawEnd, 1, 14));
    const dx = Math.abs(end.x - start.x);
    if (dx < 8) {
      const midY = (start.y + end.y) / 2;
      d = `M ${start.x} ${start.y} L ${end.x} ${end.y}`;
      labelAt = { x: start.x + 10, y: midY };
    } else if (fromId === "arbiter" || toId === "arbiter") {
      const midY = (start.y + end.y) / 2;
      d = `M ${start.x} ${start.y} L ${start.x} ${midY} L ${end.x} ${midY} L ${end.x} ${end.y}`;
      labelAt = { x: (start.x + end.x) / 2, y: midY - 6 };
    } else if (fromId === "rewrite" && toId === "llm") {
      // Straight Grade·rewrite → LLM (no elbow / bezier through pass 2)
      d = `M ${start.x} ${start.y} L ${end.x} ${end.y}`;
      labelAt = {
        x: (start.x + end.x) / 2,
        y: (start.y + end.y) / 2 - 8,
      };
    } else {
      const midY = (start.y + end.y) / 2;
      d = `M ${start.x} ${start.y} C ${start.x} ${midY}, ${end.x} ${midY}, ${end.x} ${end.y}`;
      labelAt = { x: (start.x + end.x) / 2, y: midY - 5 };
    }
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

function fusionChannelLabel(hit) {
  const ch =
    hit?.channel ||
    hit?.fusion?.channels ||
    (hit?.fusion?.dense_rank && hit?.fusion?.sparse_rank
      ? "both"
      : hit?.fusion?.sparse_rank
        ? "sparse"
        : hit?.fusion?.dense_rank
          ? "dense"
          : null);
  if (!ch) return null;
  if (ch === "both") return "dense+BM25";
  if (ch === "sparse") return "BM25 only";
  if (ch === "dense") return "dense only";
  return String(ch);
}

function RagHit({ hit, index }) {
  const [open, setOpen] = useState(false);
  const rank = hit.rank ?? index + 1;
  const body = hit.text || hit.snippet || "";
  const labeled = labeledChannelScore(hit);
  const passLabel = rankedPassLabel(hit);
  const why = hit.rerank?.why || null;
  const channel = fusionChannelLabel(hit);
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
          {passLabel && (
            <code title="Pack slot and labeled channel score">
              {passLabel}
            </code>
          )}
        </div>
        <span className="bn-hit-src">{hit.source}</span>
        {channel ? (
          <p className="bn-hit-snip bn-hit-why">
            Retrieve: {channel}
            {hit.fusion?.dense_rank != null
              ? ` · dense #${hit.fusion.dense_rank}`
              : ""}
            {hit.fusion?.sparse_rank != null
              ? ` · BM25 #${hit.fusion.sparse_rank}`
              : ""}
          </p>
        ) : null}
        {why ? <p className="bn-hit-snip bn-hit-why">Order: {why}</p> : null}
        {!open && body ? <p className="bn-hit-snip">{body}</p> : null}
        {hit.char_count != null ? (
          <div className="bn-hit-meta">
            {hit.char_count} chars
            {labeled ? ` · ${labeled}` : ""}
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

/** Highlight BM25 tokens as whole terms — never substring "i" inside "with". */
function highlightTokens(text, tokens) {
  const body = text || "";
  const toks = (tokens || [])
    .map((t) => String(t || "").trim())
    .filter((t) => t && !(t.length === 1 && /[a-z0-9]/i.test(t)))
    .sort((a, b) => b.length - a.length);
  if (!body || !toks.length) return body;
  const parts = toks.map((t) => {
    const esc = t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    // Latin / digits: word boundaries. CJK bigrams: literal run (no \b).
    if (/^[\u4e00-\u9fff]+$/.test(t)) return esc;
    return `\\b${esc}\\b`;
  });
  const re = new RegExp(`(${parts.join("|")})`, "gi");
  return body.split(re).map((part, i) =>
    i % 2 === 1 ? (
      <mark key={`tok-${i}`} className="bn-tok-hit">
        {part}
      </mark>
    ) : (
      <span key={`t-${i}`}>{part}</span>
    )
  );
}

function ChannelHit({ hit, index, highlight = false }) {
  const [open, setOpen] = useState(false);
  const body = hit.text || hit.snippet || "";
  const tokens = hit.matched_tokens || hit.query_tokens || [];
  const kind =
    hit.channel === "sparse" || highlight
      ? "BM25"
      : hit.channel === "dense"
        ? "dense"
        : channelScoreKind(hit) || "score";
  const score = scoreLabel(hit.score);
  const labeled = score ? `${kind} ${score}` : null;
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
            <span className="bn-hit-rank">#{hit.rank ?? index + 1}</span>{" "}
            {hit.title || hit.source || `Row ${index + 1}`}
          </strong>
          {labeled ? <code title={`${kind} channel score`}>{labeled}</code> : null}
        </div>
        {hit.source ? <span className="bn-hit-src">{hit.source}</span> : null}
            {highlight && tokens.length ? (
          <p className="bn-hit-snip bn-hit-why">
            Matched: {tokens.slice(0, 12).join(" · ")}
            {tokens.length > 12 ? "…" : ""}
          </p>
        ) : null}
        {!open && body ? (
          <p className="bn-hit-snip">
            {highlight ? highlightTokens(body.slice(0, 220), tokens) : body.slice(0, 220)}
          </p>
        ) : null}
        <span className="bn-hit-expand-hint">
          {open ? "Collapse chunk" : "Expand full chunk"}
        </span>
      </button>
      {open && body ? (
        <pre className="bn-hit-full">
          {highlight ? highlightTokens(body, tokens) : body}
        </pre>
      ) : null}
    </li>
  );
}

/** Expandable pool for dense_top / sparse_top. */
function ChannelPoolPanel({ title, query, rows, emptyHint, meta, highlightTokens: doHl }) {
  const list = rows || [];
  return (
    <>
      {title ? <p className="bn-rules-hint">{title}</p> : null}
      {query ? (
        <ul className="bn-fact-list">
          <li>
            <span>Query</span>
            <code>{query}</code>
          </li>
          {meta
            ? Object.entries(meta).map(([k, v]) => (
                <li key={k}>
                  <span>{k}</span>
                  <code>{v ?? "—"}</code>
                </li>
              ))
            : null}
        </ul>
      ) : null}
      {!list.length ? (
        <Skipped>{emptyHint || "No rows in this channel pool."}</Skipped>
      ) : (
        <ul className="bn-hits">
          {list.map((h, i) => (
            <ChannelHit
              key={`${h.source || h.id || i}-${i}`}
              hit={h}
              index={i}
              highlight={Boolean(doHl)}
            />
          ))}
        </ul>
      )}
    </>
  );
}

function SlotDstCard({ title, dst, regexNote }) {
  if (!dst) return null;
  const filled = dst.filled || {};
  const how = dst.how || [];
  return (
    <div className="bn-dst-card">
      <div className="bn-match-summary-kicker">{title}</div>
      <p className="bn-rules-hint" style={{ marginTop: 4 }}>
        Regex / detectors first, then cheap LLM structured JSON for missing
        slots. FSM still owns asks + tools.
      </p>
      {how.length ? (
        <ol className="bn-select-steps">
          {how.map((line, i) => (
            <li key={`how-${i}`}>{line}</li>
          ))}
        </ol>
      ) : null}
      <ul className="bn-fact-list">
        <li>
          <span>Called</span>
          <code>{dst.called ? "yes" : "no"}</code>
        </li>
        <li>
          <span>Result</span>
          <code>
            {dst.ok ? "filled" : dst.reason || "—"}
            {dst.confidence != null ? ` · conf ${dst.confidence}` : ""}
          </code>
        </li>
        {dst.missing_asked?.length ? (
          <li>
            <span>Asked to fill</span>
            <code>{dst.missing_asked.join(", ")}</code>
          </li>
        ) : null}
        {Object.keys(filled).length ? (
          <li>
            <span>Merged slots</span>
            <code>
              {Object.entries(filled)
                .map(([k, v]) => `${k}=${v}`)
                .join(" · ")}
            </code>
          </li>
        ) : null}
        {regexNote ? (
          <li>
            <span>Regex</span>
            <code>{regexNote}</code>
          </li>
        ) : null}
      </ul>
      <StageIO
        request={{
          missing: dst.missing_asked || null,
          prior: dst.prior || null,
        }}
        payload={dst}
      />
    </div>
  );
}

function ChromaHitsPanel({
  title,
  query,
  hits,
  rag,
  hybrid,
  stripExtra,
  emptyHint,
}) {
  const list = hits || [];
  const hy = hybrid || rag?.hybrid || null;
  const nums = list
    .map((h) => Number(h.score))
    .filter((n) => Number.isFinite(n));
  const stats = {
    rank1: scoreLabel(list[0]?.score),
    max: nums.length ? scoreLabel(Math.max(...nums)) : null,
  };
  return (
    <>
      {title ? <p className="bn-rules-hint">{title}</p> : null}
      {query ? (
        <ul className="bn-fact-list">
          <li>
            <span>Query</span>
            <code>{query}</code>
          </li>
        </ul>
      ) : null}
      {hy ? (
        <ul className="bn-fact-list">
          <li>
            <span>Retrieve</span>
            <code>{hy.mode || rag?.retrieve_mode || "—"}</code>
          </li>
          {isChannelPack(hy) ? (
            <>
              <li>
                <span>Dense pool</span>
                <code>{hy.dense_count ?? "—"}</code>
              </li>
              <li>
                <span>BM25 pool</span>
                <code>{hy.sparse_count ?? "—"}</code>
              </li>
              <li>
                <span>Pack</span>
                <code>
                  dense {hy.dense_kept ?? hy.dense_n ?? "—"} + BM25{" "}
                  {hy.sparse_added ?? "—"} → {hy.packed_count ?? hy.keep_k ?? "—"}
                </code>
              </li>
              <li>
                <span>Channels</span>
                <code>
                  both {hy.both ?? 0} · dense {hy.dense_only ?? 0} · BM25{" "}
                  {hy.sparse_only ?? 0}
                </code>
              </li>
            </>
          ) : (
            <li>
              <span>Note</span>
              <code>{hy.reason || "dense only"}</code>
            </li>
          )}
        </ul>
      ) : null}
      <div className="bn-rag-strip">
        <span>hits {list.length}</span>
        <span>#1 {stats.rank1 ?? "—"}</span>
        <span>max {stats.max ?? "—"}</span>
        {hy?.mode ? <span>{hy.mode}</span> : null}
        {rag?.embedding_mode ? <span>{rag.embedding_mode}</span> : null}
        {stripExtra}
      </div>
      {!list.length ? (
        <Skipped>{emptyHint || "No chunks for this pass."}</Skipped>
      ) : (
        <>
          <StageIO
            request={{
              query: query || null,
              hybrid: hy
                ? {
                    mode: hy.mode,
                    dense_n: hy.dense_n,
                    keep_k: hy.keep_k,
                    dense_count: hy.dense_count,
                    sparse_count: hy.sparse_count,
                  }
                : null,
            }}
            payload={{
              packed: list.map((h) => ({
                rank: h.rank,
                source: h.source,
                score: h.score,
                title: h.title,
                channel: fusionChannelLabel(h),
                slot: h.pack?.slot ?? null,
                dense_rank: h.fusion?.dense_rank ?? null,
                sparse_rank: h.fusion?.sparse_rank ?? null,
                order: h.rerank?.why || null,
              })),
              dense_top: hy?.dense_top || null,
              sparse_top: hy?.sparse_top || null,
            }}
          />
          <ul className="bn-hits">
            {list.map((h, i) => (
              <RagHit key={`${h.source}-${h.rank || i}`} hit={h} index={i} />
            ))}
          </ul>
        </>
      )}
    </>
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
      const weakHits = rag?.pass1_hits || [];
      const ran = Boolean(
        hits.length || enrichMode || path === "rag" || qr || grade
      );
      if (!ran) {
        return <Skipped>Pass-1 retrieve did not run — nothing to grade.</Skipped>;
      }
      return (
        <>
          <p className="bn-rules-hint">
            Grade pass-1 chunks (Correct / Ambiguous / Incorrect). Only if not
            Correct and prior chat exists: cheap-rewrite once, then optional
            pass-2 retrieve. CRAG-lite · max 1 retry.
          </p>
          <ul className="bn-fact-list">
            <li>
              <span>Grade</span>
              <code>
                {grade?.grade || qr?.grade || "—"}
                {grade?.why || qr?.grade_why
                  ? ` · ${grade?.why || qr?.grade_why}`
                  : ""}
              </code>
            </li>
            <li>
              <span>Rewrite</span>
              <code>
                {qr?.rewritten
                  ? "yes"
                  : qr?.called
                    ? qr?.reason || "called"
                    : qr?.reason || "no"}
              </code>
            </li>
            <li>
              <span>Pass-2</span>
              <code>
                {qr?.retried
                  ? `fired · kept ${qr?.kept || "—"}`
                  : "not fired"}
              </code>
            </li>
          </ul>
          <StageIO
            request={{
              pass1_query: rag?.pass1_query || inbound,
              prior: priorPreview || "(no prior turns)",
            }}
            payload={{
              grade: grade || { grade: qr?.grade, why: qr?.grade_why },
              rewrite: qr
                ? {
                    rewritten: qr.rewritten,
                    query: qr.query,
                    reason: qr.reason,
                    retried: qr.retried,
                    kept: qr.kept,
                  }
                : null,
            }}
          />
          {qr?.rewritten ? (
            <ul className="bn-fact-list">
              <li>
                <span>Rewritten query</span>
                <code>{qr.pass2_query || qr.query}</code>
              </li>
            </ul>
          ) : null}
          {(grade?.grade || qr?.grade) &&
          (grade?.grade || qr?.grade) !== "correct" &&
          weakHits.length > 0 ? (
            <ChromaHitsPanel
              title="Pass-1 chunks graded not enough (inspect):"
              query={rag?.pass1_query || inbound}
              hits={weakHits}
              rag={rag}
            />
          ) : null}
          {llmCalls
            .filter((c) => c.role === "query_rewrite")
            .map((c, i) => (
              <LlmCallCard key={`qr-${i}`} call={c} defaultOpen />
            ))}
        </>
      );
    }

    case "router": {
      const whyShort =
        path === "rag"
          ? "No tool owned this turn → RAG"
          : path === "fsm"
            ? routing?.dialogue_active || routing?.cancel_active
              ? `Sticky ${route || "fsm"}`
              : `Tool route · ${route || "fsm"}`
            : "—";
      return (
        <>
          <p className="bn-rules-hint">
            One decision: <strong>FSM</strong> (tools / sticky) or{" "}
            <strong>RAG</strong> (retrieve → generate), from detector scores
            {arbiter?.called ? " + arbiter" : ""}.
          </p>
          <MatchSummaryCard summary={matchSummary} />
          <ul className="bn-fact-list">
            <li>
              <span>Branch</span>
              <code>
                {path === "rag" ? "RAG" : path === "fsm" ? "FSM" : "—"}
                {route ? ` · ${route}` : ""}
              </code>
            </li>
            <li>
              <span>Why</span>
              <code>{whyShort}</code>
            </li>
            {routing?.debug?.action_policy ? (
              <li>
                <span>Action policy</span>
                <code>
                  {routing.debug.action_policy.called
                    ? `${routing.debug.action_policy.act || "answer"} · ${
                        routing.debug.action_policy_override
                          ? `${routing.debug.action_policy_override.from} → ${routing.debug.action_policy_override.to}`
                          : "kept RAG"
                      }${
                        routing.debug.action_policy.confidence != null
                          ? ` · ${Number(routing.debug.action_policy.confidence).toFixed(2)}`
                          : ""
                      }`
                    : `skipped · ${routing.debug.action_policy.gate || routing.debug.action_policy.reason || "n/a"}`}
                </code>
              </li>
            ) : null}
            {routing?.hours_followup_promoted ? (
              <li>
                <span>Hours follow-up</span>
                <code>promoted</code>
              </li>
            ) : null}
          </ul>
          <StageIO
            request={{ scores: routing?.scores || {} }}
            payload={{
              branch: path || null,
              route: route || null,
              why: whyShort,
            }}
          />
        </>
      );
    }

    case "rules": {
      const scoreRows = Object.entries(routing?.scores || {}).sort(
        (a, b) => Number(b[1]) - Number(a[1])
      );
      const rulesByScore = [...rulesEvaluated].sort((a, b) => {
        const sa = Number(routing?.scores?.[a.id] ?? a.score ?? -Infinity);
        const sb = Number(routing?.scores?.[b.id] ?? b.score ?? -Infinity);
        return sb - sa;
      });
      return (
        <>
          <p className="bn-rules-hint">
            Detector awards for each rule (not RAG similarity). Highest on top;
            winner feeds the router.
          </p>
          {scoreRows.length > 0 ? (
            <ul className="bn-fact-list">
              {scoreRows.map(([k, v]) => {
                const origin =
                  routing.score_origins?.[k] ||
                  scoreLedger.filter((e) => e.route === k).slice(-1)[0];
                const isTop = k === scoreRows[0][0];
                return (
                  <li key={k}>
                    <span>
                      {isTop ? "★ " : ""}
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
          ) : (
            <Skipped>No detector scores this turn.</Skipped>
          )}
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
          {rulesByScore.length > 0 ? (
            <ul className="bn-rule-list">
              {rulesByScore.map((r) => (
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
    }

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
            Tool / sticky dialogue for <code>{route || "—"}</code>. Downstream
            nodes own the LLM work: <em>DST · cheap JSON</em> (slots),{" "}
            <em>Final check</em> (proceed / clarify / abort),{" "}
            <em>Staff email</em> (inbox draft on escalate).
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
            {route === "staff_escalation" || trace?.escalation ? (
              <li>
                <span>Escalation</span>
                <code>
                  {(trace?.escalation || trace?.debug?.escalation)?.category ||
                    "staff"}
                  {(trace?.escalation || trace?.debug?.escalation)?.priority
                    ? ` · ${(trace?.escalation || trace?.debug?.escalation).priority}`
                    : ""}
                </code>
              </li>
            ) : null}
            {finalCheck?.action ? (
              <li>
                <span>Final check</span>
                <code>
                  {finalCheck.action} → see Final check node
                </code>
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
              escalation: trace?.escalation || trace?.debug?.escalation || null,
              grounding: grounding || null,
            }}
          />
        </>
      );

    case "gate": {
      if (path !== "fsm") {
        return <Skipped>Final check runs on FSM commit / escalate turns.</Skipped>;
      }
      const gateCalls = llmCalls.filter((c) => isFinalCheckCall(c));
      const ran = Boolean(finalCheck?.called || gateCalls.length);
      if (!ran) {
        return (
          <Skipped>
            No final-check LLM this turn (commit gate not required, or skipped).
          </Skipped>
        );
      }
      return (
        <>
          <p className="bn-rules-hint">
            <strong>Preliminary · Final check</strong> — cheap JSON gate before
            a book / cancel / escalate side-effect. Not the parent-facing
            DashScope generate.
          </p>
          <ul className="bn-fact-list">
            <li>
              <span>Action</span>
              <code>{finalCheck?.action || "—"}</code>
            </li>
            <li>
              <span>Reason</span>
              <code>{finalCheck?.reason || "—"}</code>
            </li>
            {finalCheck?.model ? (
              <li>
                <span>Model</span>
                <code>{finalCheck.model}</code>
              </li>
            ) : null}
          </ul>
          <StageIO
            request={{ kind: route, message: inbound }}
            payload={finalCheck || null}
          />
          {gateCalls.map((c, i) => (
            <LlmCallCard
              key={`gate-llm-${i}`}
              call={c}
              defaultOpen={i === 0}
            />
          ))}
        </>
      );
    }

    case "staffmail": {
      if (path !== "fsm") {
        return <Skipped>Staff email draft runs on escalate turns.</Skipped>;
      }
      const mailCalls = llmCalls.filter((c) => isStaffEmailCall(c));
      const wrote = Boolean(staffMessage || mailCalls.length);
      if (!wrote) {
        return (
          <Skipped>
            No staff-email draft this turn (not escalate, or final check
            clarified / aborted).
          </Skipped>
        );
      }
      return (
        <>
          <p className="bn-rules-hint">
            <strong>Staff email draft</strong> — cheap LLM writes the Mailbox /
            boss email body. Parent still gets a template safety reply; inbox
            write lands on Staff desk.
          </p>
          <ul className="bn-fact-list">
            {staffMessage?.category ? (
              <li>
                <span>Category</span>
                <code>{staffMessage.category}</code>
              </li>
            ) : null}
            {staffMessage?.priority ? (
              <li>
                <span>Priority</span>
                <code>{staffMessage.priority}</code>
              </li>
            ) : null}
            {staffMessage?.email_provider || staffMessage?.id ? (
              <li>
                <span>Draft</span>
                <code>
                  {staffMessage.email_provider || "template"}
                  {staffMessage.id ? ` · ${staffMessage.id}` : ""}
                </code>
              </li>
            ) : null}
          </ul>
          <StageIO
            request={{ parent: inbound, route }}
            payload={staffMessage || null}
          />
          {mailCalls.map((c, i) => (
            <LlmCallCard
              key={`staffmail-llm-${i}`}
              call={c}
              defaultOpen={i === 0}
            />
          ))}
        </>
      );
    }

    case "dst": {
      const bookingDst =
        trace?.booking?.booking_dst ||
        trace?.booking?.arbiter_booking ||
        null;
      const cancelDst = trace?.cancel?.cancel_dst || null;
      const ran = Boolean(
        (bookingDst && bookingDst.called) || (cancelDst && cancelDst.called)
      );
      if (path !== "fsm") {
        return <Skipped>DST runs on FSM booking / cancel turns.</Skipped>;
      }
      if (!ran) {
        return (
          <Skipped>
            No slot-DST call this turn (regex filled everything, or dialogue did
            not need identity/schedule slots).
          </Skipped>
        );
      }
      const regexNote = bookingDst
        ? [
            bookingDst.regex_identity &&
            Object.keys(bookingDst.regex_identity).length
              ? `identity ${JSON.stringify(bookingDst.regex_identity)}`
              : "identity {}",
            bookingDst.regex_course
              ? `course=${bookingDst.regex_course}`
              : "course=—",
          ].join(" · ")
        : null;
      return (
        <>
          <p className="bn-rules-hint">
            <strong>Preliminary · Dialogue State Tracking</strong> — cheap
            schema JSON after regex (not the parent-facing DashScope generate).
            Merges only supported slots; FSM templates still ask for gaps and
            commit books/cancels. Open the cards below for the JSON prompt /
            response.
          </p>
          {bookingDst?.called ? (
            <SlotDstCard
              title="Booking DST"
              dst={bookingDst}
              regexNote={regexNote}
            />
          ) : null}
          {cancelDst?.called ? (
            <SlotDstCard title="Cancel DST" dst={cancelDst} />
          ) : null}
          {llmCalls
            .filter((c) => DST_LLM_ROLES.has(c.role))
            .map((c, i) => (
              <LlmCallCard
                key={`dst-llm-${i}`}
                call={c}
                defaultOpen={i === 0}
              />
            ))}
        </>
      );
    }

    case "rag":
      if (path !== "rag") {
        return (
          <Skipped>
            RAG path not taken
            {enrichMode ? " (FSM may still enrich via hybrid retrieve)." : "."}
          </Skipped>
        );
      }
      return (
        <>
          <p className="bn-rules-hint">
            Retrieve → generate. The flowchart expands hybrid as{" "}
            <strong>Dense ∥ Sparse → channel pack (3+2) → ranked pass 1</strong>,
            then grade / optional rewrite → pass 2. Generation still sees the
            parent’s wording + chat history.
          </p>
          <ul className="bn-fact-list">
            <li>
              <span>Retrieve mode</span>
              <code>
                {rag?.retrieve_mode || rag?.hybrid?.mode || "—"}
              </code>
            </li>
            <li>
              <span>Hits kept</span>
              <code>
                {rag?.hit_count ?? hits.length}/{rag?.top_k ?? "—"}
              </code>
            </li>
            <li>
              <span>Rank #1 score</span>
              <code>
                {rankedPassLabel(hits[0]) ||
                  (rag?.rank1_score != null || rag?.top_score != null
                    ? `score ${scoreLabel(rag?.rank1_score ?? rag?.top_score)}`
                    : null) ||
                  "—"}
              </code>
            </li>
            <li>
              <span>Best channel score in set</span>
              <code>
                {rag?.max_score != null
                  ? `${channelScoreKind(hits[0]) || "score"} ${scoreLabel(rag.max_score)}`
                  : "—"}
              </code>
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

    case "dense": {
      const hy = rag?.hybrid;
      const ran = path === "rag" || enrichMode || hy || hits.length;
      if (!ran) return <Skipped>Dense retrieve did not run.</Skipped>;
      return (
        <ChannelPoolPanel
          title="Dense channel · Chroma vector search (embeddings). Click a row to expand the chunk."
          query={rag?.pass1_query || inbound}
          rows={hy?.dense_top || []}
          meta={{
            Pool: hy?.dense_count ?? "—",
            Index: rag?.index_size ?? "—",
            Mode: rag?.embedding_mode || "—",
          }}
          emptyHint={
            hy?.mode && !isChannelPack(hy)
              ? `Dense-only / fallback: ${hy.reason || hy.mode}`
              : "No dense pool rows in debug payload."
          }
        />
      );
    }

    case "sparse": {
      const hy = rag?.hybrid;
      const ran = path === "rag" || enrichMode || hy || hits.length;
      if (!ran) return <Skipped>Sparse retrieve did not run.</Skipped>;
      return (
        <ChannelPoolPanel
          title="Sparse channel · in-process BM25 over the same KB rows. Click a row to expand; matched query tokens are highlighted."
          query={rag?.pass1_query || inbound}
          rows={hy?.sparse_top || []}
          meta={{
            Pool: hy?.sparse_count ?? "—",
            Index: rag?.sparse_index_size ?? "—",
          }}
          highlightTokens
          emptyHint={
            hy?.mode && !isChannelPack(hy)
              ? `BM25 skipped: ${hy.reason || hy.mode}`
              : "No BM25 pool rows in debug payload."
          }
        />
      );
    }

    case "fuse": {
      const hy = rag?.hybrid;
      const ran = path === "rag" || enrichMode || hy || hits.length;
      const pass1 = rag?.pass1_hits || hits;
      if (!ran) return <Skipped>Channel pack did not run.</Skipped>;
      if (hy && !isChannelPack(hy)) {
        return (
          <>
            <p className="bn-rules-hint">
              Channel pack not used this turn — fell back to a single channel.
            </p>
            <ul className="bn-fact-list">
              <li>
                <span>Mode</span>
                <code>{hy.mode || "—"}</code>
              </li>
              <li>
                <span>Reason</span>
                <code>{hy.reason || "—"}</code>
              </li>
            </ul>
          </>
        );
      }
      const how =
        hy?.how ||
        [
          "Dense Chroma ranks by embedding similarity (paraphrase).",
          "BM25 ranks by keyword / CJK token overlap (exact terms).",
          `Pack keep_k=${rag?.top_k ?? hy?.keep_k ?? 5}: take dense ranks 1–${hy?.dense_n ?? 3}, then fill from BM25 (skip duplicates).`,
          "Age-band files may still be injected when the parent names an age.",
        ];
      const preview = hy?.packed_preview || hy?.fused_preview || [];
      return (
        <>
          <p className="bn-rules-hint">
            <strong>
              How the final {rag?.top_k ?? (pass1.length || 5)} chunks are
              chosen
            </strong>
          </p>
          <ol className="bn-select-steps">
            {how.map((line, i) => (
              <li key={`fuse-how-${i}`}>{line}</li>
            ))}
          </ol>
          <p className="bn-rules-hint">
            <strong>Channel pack</strong> — dense primary for paraphrase, BM25
            supplements for exact centre terms. No multi-flag lexical sort.
          </p>
          <ul className="bn-fact-list">
            <li>
              <span>Dense slots</span>
              <code>
                ranks 1–{hy?.dense_n ?? 3} (kept {hy?.dense_kept ?? "—"})
              </code>
            </li>
            <li>
              <span>BM25 fill</span>
              <code>
                remaining slots · added {hy?.sparse_added ?? "—"} · dupes skipped{" "}
                {hy?.skipped_dupes ?? "—"}
              </code>
            </li>
            <li>
              <span>Pools</span>
              <code>
                dense {hy?.dense_count ?? "—"} · BM25 {hy?.sparse_count ?? "—"}
              </code>
            </li>
            <li>
              <span>In pack</span>
              <code>
                both {hy?.both ?? 0} · dense {hy?.dense_only ?? 0} · BM25{" "}
                {hy?.sparse_only ?? 0}
              </code>
            </li>
            <li>
              <span>Keep</span>
              <code>
                top_k {rag?.top_k ?? hy?.keep_k ?? "—"} · packed{" "}
                {hy?.packed_count ?? "—"} · pool{" "}
                {rag?.pool_returned ?? "—"}/{rag?.pool_k ?? "—"}
              </code>
            </li>
          </ul>
          {preview.length ? (
            <>
              <p className="bn-rules-hint">Packed keep order</p>
              <ul className="bn-fact-list">
                {preview.slice(0, 8).map((row) => (
                  <li key={`fp-${row.rank}-${row.source}`}>
                    <span>
                      #{row.rank} {row.title || row.source || "chunk"}
                    </span>
                    <code>
                      {row.slot || "slot"}
                      {row.dense_rank != null ? ` · d#${row.dense_rank}` : ""}
                      {row.sparse_rank != null ? ` · s#${row.sparse_rank}` : ""}
                      {row.channels ? ` · ${row.channels}` : ""}
                    </code>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          {pass1.length ? (
            <>
              <p className="bn-rules-hint">
                Kept for pass 1 — why each row is in the pack
              </p>
              <ul className="bn-fact-list">
                {pass1.map((h, i) => (
                  <li key={`kept-${h.source}-${i}`}>
                    <span>
                      #{h.rank ?? i + 1} {h.title || h.source || "hit"}
                    </span>
                    <code>
                      {h.pack?.slot || h.rerank?.why || "packed"}
                      {h.fusion?.dense_rank != null
                        ? ` · d#${h.fusion.dense_rank}`
                        : ""}
                      {h.fusion?.sparse_rank != null
                        ? ` · s#${h.fusion.sparse_rank}`
                        : ""}
                    </code>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          <StageIO
            request={{
              query: rag?.pass1_query || inbound,
              dense_n: hy?.dense_n ?? 3,
              keep_k: hy?.keep_k ?? rag?.top_k ?? null,
            }}
            payload={{
              hybrid: hy || null,
              kept_top_k: rag?.top_k ?? null,
              pool_returned: rag?.pool_returned ?? null,
              kept: pass1.map((h) => ({
                rank: h.rank,
                source: h.source,
                slot: h.pack?.slot ?? null,
                dense_rank: h.fusion?.dense_rank ?? null,
                sparse_rank: h.fusion?.sparse_rank ?? null,
                order: h.rerank?.why || null,
              })),
            }}
          />
        </>
      );
    }

    case "chroma": {
      const pass1 = rag?.pass1_hits || hits;
      if (!(pass1.length || enrichMode || path === "rag")) {
        return <Skipped>Pass-1 KB retrieve did not run.</Skipped>;
      }
      return (
        <>
          <p className="bn-rules-hint">
            <strong>Ranked pass 1</strong> after channel pack (3 dense + BM25
            fill). Always first with the <strong>raw parent message</strong> —
            grade sees these chunks next.
          </p>
          <ChromaHitsPanel
            title={
              enrichMode
                ? "Pass 1 · fused enrich hits"
                : "Pass 1 · fused raw-query hits"
            }
            query={rag?.pass1_query || inbound}
            hits={pass1}
            rag={rag}
            hybrid={rag?.hybrid}
            stripExtra={
              <>
                <span>
                  pool {rag?.pool_returned ?? "—"}/{rag?.pool_k ?? "—"}
                </span>
                <span>top_k {rag?.top_k ?? "—"}</span>
                <span>chroma {rag?.index_size ?? "—"}</span>
                <span>BM25 {rag?.sparse_index_size ?? "—"}</span>
              </>
            }
            emptyHint="Pass 1 returned no chunks."
          />
        </>
      );
    }

    case "chroma2": {
      const retried = Boolean(rag?.query_rewrite?.retried);
      const pass2 = rag?.pass2_hits || [];
      if (!(path === "rag" || enrichMode || hits.length)) {
        return <Skipped>RAG path not taken.</Skipped>;
      }
      if (!retried) {
        return (
          <Skipped>
            Pass 2 not fired — pass-1 grade was enough (or no rewrite).
          </Skipped>
        );
      }
      return (
        <>
          <p className="bn-rules-hint">
            Optional second retrieve with the <strong>rewritten</strong> query
            after pass-1 was graded not Correct. Final answer uses the better of
            pass 1 / pass 2 (kept: {rag?.final_pass || rag?.query_rewrite?.kept || "—"}
            ).
          </p>
          <ChromaHitsPanel
            title="Pass 2 · hybrid rewritten-query retrieve"
            query={rag?.pass2_query || rag?.query_rewrite?.query}
            hits={pass2}
            rag={rag}
            hybrid={rag?.hybrid}
            emptyHint="Pass 2 fired but returned no chunks."
          />
        </>
      );
    }

    case "llm": {
      const genCalls = llmCalls.filter((c) => isMainLlmCall(c));
      const dstOnly =
        path === "fsm" &&
        llmCalls.some((c) => DST_LLM_ROLES.has(c.role)) &&
        genCalls.length === 0;
      return (
        <>
          <p className="bn-rules-hint">
            Parent-facing generate via DashScope (Qwen). Slot-DST JSON lives on{" "}
            <em>DST · cheap JSON</em> — not here.
          </p>
          {genCalls.length === 0 ? (
            <Skipped>
              {dstOnly
                ? "No parent-facing LLM this turn — booking/cancel used FSM templates after DST JSON. Open the DST node for the preliminary cheap call."
                : "No LLM call (template / deterministic reply)."}
            </Skipped>
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
        return (
          <Skipped>
            No staff-desk write this turn (SheetPro todo row / Mailbox message /
            booking CSV).
          </Skipped>
        );
      }
      return (
        <>
          <p className="bn-rules-hint">
            Staff desk side-effects: SheetPro admin todos, Mailbox / WhatsApp
            staff notes, and booking/cancel CSV rows when a flow commits.
          </p>
          <StageIO
            request={{ route, session: trace.session_id }}
            payload={{
              admin_todo: adminTodo || null,
              staff_message: staffMessage || null,
              booking: trace?.booking?.booking || null,
              cancel: trace?.cancel || null,
            }}
          />
        </>
      );
    }

    case "answer":
      return (
        <>
          <p className="bn-rules-hint">
            What the parent sees after sanitize (gap tags stripped). Free-text
            model I/O is on <em>LLM (DashScope)</em>; booking/cancel slot JSON is
            on <em>DST · cheap JSON</em>.
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
    const genN = llmCalls.filter((c) => isMainLlmCall(c)).length;
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
        label: "Router · FSM|RAG",
        kind: "router",
        shape: "decision",
        status: hasTrace && path ? "done" : hasTrace ? "done" : "idle",
      },
      {
        id: "fsm",
        label: "FSM · tools",
        kind: "hard",
        shape: "process",
        status: !hasTrace ? "idle" : path === "fsm" ? "done" : "skipped",
      },
      {
        id: "dst",
        label: "DST · cheap JSON",
        kind: "llm",
        shape: "process",
        status: !hasTrace
          ? "idle"
          : path !== "fsm"
            ? "skipped"
            : (trace?.booking?.booking_dst || trace?.booking?.arbiter_booking)
                  ?.called || trace?.cancel?.cancel_dst?.called
              ? (trace?.booking?.booking_dst || trace?.booking?.arbiter_booking)
                  ?.ok || trace?.cancel?.cancel_dst?.ok
                ? "done"
                : "empty"
              : route === "booking_tool" ||
                  route === "cancel_dialogue" ||
                  trace?.booking ||
                  trace?.cancel
                ? "skipped"
                : "skipped",
      },
      {
        id: "gate",
        label: "Final check",
        kind: "llm",
        shape: "process",
        status: !hasTrace
          ? "idle"
          : path !== "fsm"
            ? "skipped"
            : finalCheck?.called ||
                llmCalls.some((c) => isFinalCheckCall(c))
              ? "done"
              : route === "staff_escalation" ||
                  trace?.booking?.stage === "booked" ||
                  trace?.cancel?.stage === "cancelled" ||
                  finalCheck
                ? finalCheck && !finalCheck.called
                  ? "skipped"
                  : "empty"
                : "skipped",
      },
      {
        id: "staffmail",
        label: "Staff email",
        kind: "llm",
        shape: "process",
        status: !hasTrace
          ? "idle"
          : path !== "fsm"
            ? "skipped"
            : staffMessage || llmCalls.some((c) => isStaffEmailCall(c))
              ? "done"
              : route === "staff_escalation"
                ? "empty"
                : "skipped",
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
        id: "dense",
        label: "Dense · Chroma",
        kind: "store",
        shape: "cylinder",
        status: !hasTrace
          ? "idle"
          : !(path === "rag" || enrichMode || hits.length || rag?.hybrid)
            ? "skipped"
            : rag?.hybrid?.dense_count > 0 || (rag?.hybrid?.dense_top || []).length
              ? "done"
              : path === "rag" || enrichMode
                ? "empty"
                : "skipped",
      },
      {
        id: "sparse",
        label: "Sparse · BM25",
        kind: "store",
        shape: "cylinder",
        status: !hasTrace
          ? "idle"
          : !(path === "rag" || enrichMode || hits.length || rag?.hybrid)
            ? "skipped"
            : rag?.hybrid?.sparse_count > 0 ||
                (rag?.hybrid?.sparse_top || []).length
              ? "done"
              : path === "rag" || enrichMode
                ? isChannelPack(rag?.hybrid, rag)
                  ? "empty"
                  : "skipped"
                : "skipped",
      },
      {
        id: "fuse",
        label: "Pack · 3+2",
        kind: "rag",
        shape: "process",
        status: !hasTrace
          ? "idle"
          : !(path === "rag" || enrichMode || hits.length || rag?.hybrid)
            ? "skipped"
            : isChannelPack(rag?.hybrid, rag) ||
                (rag?.pass1_hits || hits).length
              ? "done"
              : path === "rag" || enrichMode
                ? "empty"
                : "skipped",
      },
      {
        id: "chroma",
        label: "Ranked · pass 1",
        kind: "store",
        shape: "cylinder",
        status: !hasTrace
          ? "idle"
          : (rag?.pass1_hits || hits).length || rag?.pass1_hit_count
            ? "done"
            : path === "rag" || enrichMode
              ? "empty"
              : "skipped",
      },
      {
        id: "rewrite",
        label: "Grade · rewrite",
        kind: "llm",
        shape: "process",
        status: !hasTrace
          ? "idle"
          : !(path === "rag" || enrichMode || hits.length)
            ? "skipped"
            : rag?.retrieval_grade || rag?.query_rewrite
              ? "done"
              : "skipped",
      },
      {
        id: "chroma2",
        label: "KB · pass 2",
        kind: "store",
        shape: "cylinder",
        status: !hasTrace
          ? "idle"
          : !(path === "rag" || enrichMode || hits.length)
            ? "skipped"
            : rag?.query_rewrite?.retried
              ? (rag?.pass2_hits || []).length
                ? "done"
                : "empty"
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
        label: "Staff desk · Sheet/Mail",
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
    rag?.hybrid,
    enrichMode,
    llmCalls,
    adminTodo,
    staffMessage,
    finalCheck,
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
      "answer",
    ]);
    if (history?.enabled === false) lit.delete("memory");
    if (arbiter?.called) lit.add("arbiter");
    const lightHybrid = () => {
      lit.add("dense");
      lit.add("sparse");
      lit.add("fuse");
      lit.add("chroma");
      lit.add("rewrite");
      if (rag?.query_rewrite?.retried) lit.add("chroma2");
    };
    if (path === "fsm") {
      lit.add("fsm");
      if (
        (trace?.booking?.booking_dst || trace?.booking?.arbiter_booking)?.called ||
        trace?.cancel?.cancel_dst?.called
      ) {
        lit.add("dst");
      }
      if (finalCheck?.called || llmCalls.some((c) => isFinalCheckCall(c))) {
        lit.add("gate");
      }
      if (staffMessage || llmCalls.some((c) => isStaffEmailCall(c))) {
        lit.add("staffmail");
      }
      if (liveOn) lit.add("live");
      if (hits.length || enrichMode) lightHybrid();
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
      lightHybrid();
      if (liveOn) lit.add("live");
      if (adminTodo) lit.add("sheets");
    }
    if (llmCalls.some((c) => isMainLlmCall(c))) lit.add("llm");
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
    finalCheck,
    rag?.query_rewrite?.retried,
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
      link("arbiter", "router", "e-arb-router");
    }
    if (path === "fsm") link("router", "fsm", "e-router-fsm");
    if (path === "rag") link("router", "rag", "e-router-rag");
    if (on("fsm") && on("dst")) link("fsm", "dst", "e-fsm-dst");
    if (on("dst") && on("gate")) link("dst", "gate", "e-dst-gate");
    else if (on("fsm") && on("gate")) link("fsm", "gate", "e-fsm-gate");
    if (on("gate") && on("staffmail")) link("gate", "staffmail", "e-gate-staffmail");
    if (on("staffmail") && on("sheets"))
      link("staffmail", "sheets", "e-staffmail-sheets");
    else if (on("gate") && on("sheets") && !on("staffmail"))
      link("gate", "sheets", "e-gate-sheets");
    if (on("gate") && on("answer") && !on("llm") && !on("sheets") && !on("staffmail"))
      link("gate", "answer", "e-gate-answer");
    if (on("dst") && on("live")) link("dst", "live", "e-dst-live");
    else if (on("fsm") && on("live")) link("fsm", "live", "e-fsm-live");
    if (on("dst") && on("llm")) link("dst", "llm", "e-dst-llm");
    else if (on("fsm") && on("llm")) link("fsm", "llm", "e-fsm-llm");
    // Booking/cancel often end on FSM templates — no parent-facing DashScope call.
    if (on("dst") && on("answer") && !on("llm") && !on("gate"))
      link("dst", "answer", "e-dst-answer");
    else if (on("fsm") && on("answer") && !on("llm") && !on("sheets") && !on("gate"))
      link("fsm", "answer", "e-fsm-answer");
    if (on("fsm") && on("sheets") && !on("staffmail") && !on("gate"))
      link("fsm", "sheets", "e-fsm-sheets");
    if (on("rag") && on("dense")) link("rag", "dense", "e-rag-dense");
    if (on("rag") && on("sparse")) link("rag", "sparse", "e-rag-sparse");
    if (on("dense") && on("fuse")) link("dense", "fuse", "e-dense-fuse");
    if (on("sparse") && on("fuse")) link("sparse", "fuse", "e-sparse-fuse");
    if (on("fuse") && on("chroma")) link("fuse", "chroma", "e-fuse-chroma");
    if (on("chroma") && on("rewrite")) {
      link("chroma", "rewrite", "e-chroma-rewrite");
    }
    if (on("rewrite") && on("chroma2") && rag?.query_rewrite?.retried) {
      link("rewrite", "chroma2", "e-rewrite-chroma2");
      if (on("chroma2") && on("llm")) link("chroma2", "llm", "e-chroma2-llm");
    } else if (on("rewrite") && on("llm")) {
      link("rewrite", "llm", "e-rewrite-llm");
    }
    if (on("live") && on("llm")) link("live", "llm", "e-live-llm");
    if (on("live") && on("fuse") && enrichMode) {
      link("live", "fuse", "e-live-fuse");
    }
    if (on("llm")) link("llm", "answer", "e-llm-answer");
    if (on("sheets")) link("sheets", "answer", "e-sheets-answer");
    return lit;
  }, [
    trace,
    activePath,
    statusById,
    arbiter,
    path,
    enrichMode,
    rag?.query_rewrite?.retried,
  ]);

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
    const pass1 = rag?.pass1_hits || hits;
    const hy = rag?.hybrid;
    const labels = {
      "e-rules-router": pick ? `→ ${pick}` : "",
      "e-arb-router": arbiter?.called ? arbiter.action || "keep" : "",
      "e-router-fsm": path === "fsm" ? "FSM" : "",
      "e-router-rag": path === "rag" ? "RAG" : "",
      "e-fsm-dst":
        (trace?.booking?.booking_dst || trace?.booking?.arbiter_booking)?.called ||
        trace?.cancel?.cancel_dst?.called
          ? "slot DST"
          : "slots",
      "e-fsm-gate": finalCheck?.action
        ? `check · ${finalCheck.action}`
        : "final check",
      "e-dst-gate": "slots → gate",
      "e-gate-staffmail": "proceed → draft",
      "e-staffmail-sheets": "Mailbox",
      "e-gate-sheets": "commit",
      "e-gate-answer": finalCheck?.action
        ? String(finalCheck.action)
        : "clarify",
      "e-dst-llm": "slots → generate",
      "e-dst-answer": "slots → template",
      "e-fsm-answer": "FSM template",
      "e-rag-dense": "vectors",
      "e-rag-sparse": "keywords",
      "e-dense-fuse":
        hy?.dense_count != null ? `${hy.dense_count} hits` : "dense",
      "e-sparse-fuse":
        hy?.sparse_count != null ? `${hy.sparse_count} hits` : "BM25",
      "e-fuse-chroma": isChannelPack(hy, rag) ? "3+2 pack" : "fuse",
      "e-chroma-rewrite":
        (rag?.retrieval_grade?.grade
          ? String(rag.retrieval_grade.grade)
          : "grade") +
        (rankedPassLabel(pass1[0]) ? ` · ${rankedPassLabel(pass1[0])}` : ""),
      "e-rewrite-chroma2": rag?.query_rewrite?.retried ? "retry ×1" : "",
      "e-rewrite-llm": rag?.query_rewrite?.retried ? "" : "no retry",
      "e-chroma2-llm":
        rag?.query_rewrite?.retried && rankedPassLabel(rag?.pass2_hits?.[0])
          ? rankedPassLabel(rag.pass2_hits[0])
          : "",
      "e-live-fuse": enrichMode ? "enrich" : "",
    };
    return labels;
  }, [
    routing,
    winningRuleId,
    route,
    arbiter,
    path,
    hits,
    rag,
    enrichMode,
    trace,
    finalCheck,
  ]);

  const fitView = useCallback(() => {
    const el = viewportRef.current;
    if (!el) return false;
    // Skip while the system pane is display:none (staff desk) — avoids zoom flash.
    if (el.offsetParent === null && el.getClientRects().length === 0) return false;
    const { width, height } = el.getBoundingClientRect();
    if (width < 120 || height < 120) return false;
    const gw = FIT_FOCUS.maxX - FIT_FOCUS.minX;
    const gh = FIT_FOCUS.maxY - FIT_FOCUS.minY;
    const pad = 28;
    const nextZoom = Math.min(
      MAX_ZOOM,
      Math.max(
        MIN_ZOOM,
        Math.min((width - pad) / gw, (height - pad) / gh) * 1.08
      )
    );
    const cx = (FIT_FOCUS.minX + FIT_FOCUS.maxX) / 2;
    // Bias upward so parent/lang sit near the top of the viewport
    const cy = FIT_FOCUS.minY + (FIT_FOCUS.maxY - FIT_FOCUS.minY) * 0.38;
    const rawPan = {
      x: width / 2 - cx * nextZoom,
      y: height * 0.28 - cy * nextZoom,
    };
    const nextPan = clampPan(rawPan, nextZoom, width, height);
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
      const nextPan = clampPan(
        { x: mx - wx * next, y: my - wy * next },
        next,
        rect.width,
        rect.height
      );
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
    const el = viewportRef.current;
    const { width, height } = el?.getBoundingClientRect?.() || {
      width: 0,
      height: 0,
    };
    const next = clampPan(
      { x: d.panX + dx, y: d.panY + dy },
      zoomRef.current,
      width || 1,
      height || 1
    );
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
              const el = viewportRef.current;
              const { width, height } = el?.getBoundingClientRect?.() || {
                width: 0,
                height: 0,
              };
              const z = zoomRef.current;
              const next = Math.min(MAX_ZOOM, z * 1.12);
              const nextPan = clampPan(
                panRef.current,
                next,
                width || 1,
                height || 1
              );
              zoomRef.current = next;
              panRef.current = nextPan;
              setZoom(next);
              setPan(nextPan);
            }}
            title="Zoom in"
            aria-label="Zoom in"
          >
            +
          </button>
          <button
            type="button"
            onClick={() => {
              const el = viewportRef.current;
              const { width, height } = el?.getBoundingClientRect?.() || {
                width: 0,
                height: 0,
              };
              const z = zoomRef.current;
              const next = Math.max(MIN_ZOOM, z / 1.12);
              const nextPan = clampPan(
                panRef.current,
                next,
                width || 1,
                height || 1
              );
              zoomRef.current = next;
              panRef.current = nextPan;
              setZoom(next);
              setPan(nextPan);
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
                markerWidth="10"
                markerHeight="10"
                orient="auto-start-reverse"
                markerUnits="userSpaceOnUse"
              >
                <path d="M 0 1.2 L 9 5 L 0 8.8 Z" fill="rgba(18,169,188,0.35)" />
              </marker>
              <marker
                id="bn-flow-arrow-lit"
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="10"
                markerHeight="10"
                orient="auto-start-reverse"
                markerUnits="userSpaceOnUse"
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
