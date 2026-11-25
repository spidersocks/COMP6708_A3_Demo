import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

/** Stable empty defaults — inline `|| []` / `|| {}` as hook deps recreate every render
 *  and were re-firing fitLitPath (camera snap-back) on booking turns that refresh
 *  calendar/families while often lacking llm_calls / arbiter payloads. */
const EMPTY_ARR = Object.freeze([]);
const EMPTY_OBJ = Object.freeze({});

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
 * Live sits on the FSM column under staffmail (always in Fit). Edges to/from
 * live elbow through the left gutter — never pierce dst/gate/mail.
 */
/**
 * Layout — three-way split under Router (ISO-ish cascade):
 *   FSM dialogue (far left) · Live tools (centre-left) · RAG (right)
 * Calendar left of Live (same row); HKO directly under Live.
 * Straight ports only — no elbows into the API boxes.
 */
const LAYOUT = {
  parent: { x: 0, y: 0, w: 156, h: 42, shape: "terminal" },
  lang: { x: 0, y: 68, w: 148, h: 48, shape: "rule" },
  memory: { x: 0, y: 145, w: 148, h: 68, shape: "cylinder" },
  // Rules/scores live inside the router — one decision stage
  router: { x: 0, y: 270, w: 128, h: 128, shape: "decision" },
  // L2 sidecar — action policy and/or intent arbiter (cheap LLM)
  arbiter: { x: 210, y: 300, w: 132, h: 48, shape: "llm" },
  // Branch heads — FSM far left; live centre; RAG right
  fsm: { x: -440, y: 470, w: 148, h: 46, shape: "process" },
  live: { x: 50, y: 470, w: 148, h: 52, shape: "io" },
  rag: { x: 360, y: 470, w: 140, h: 46, shape: "process" },
  // FSM dialogue stack (far left — not shared with live APIs)
  dst: { x: -440, y: 560, w: 160, h: 48, shape: "llm" },
  gate: { x: -440, y: 650, w: 160, h: 48, shape: "llm" },
  staffmail: { x: -440, y: 740, w: 160, h: 48, shape: "llm" },
  sheets: { x: -440, y: 980, w: 160, h: 70, shape: "cylinder" },
  // Live APIs: calendar ← Live (horizontal), Live → HKO (vertical)
  calendar: { x: -170, y: 470, w: 148, h: 52, shape: "io" },
  hko: { x: 50, y: 610, w: 148, h: 52, shape: "io" },
  // RAG retrieve arm (right) — pack+rank is one process; grade vs rewrite split
  dense: { x: 260, y: 580, w: 130, h: 70, shape: "cylinder" },
  sparse: { x: 480, y: 580, w: 130, h: 70, shape: "cylinder" },
  fuse: { x: 370, y: 730, w: 176, h: 56, shape: "process" },
  grade: { x: 370, y: 850, w: 160, h: 48, shape: "rule" },
  rewrite: { x: 370, y: 950, w: 152, h: 48, shape: "llm" },
  llm: { x: 60, y: 1050, w: 156, h: 52, shape: "llm" },
  answer: { x: 60, y: 1160, w: 156, h: 42, shape: "terminal" },
};

/** Corridor X between FSM column and centre (FSM/DST → LLM). */
const FSM_LLM_X = -260;
/** Far-left gutter — elbows around the FSM stack without crossing boxes. */
const LIVE_GUTTER_X = -580;
const ARROW_SIZE = 11;

const EDGES = [
  { id: "e-parent-lang", from: "parent", to: "lang" },
  { id: "e-lang-mem", from: "lang", to: "memory" },
  { id: "e-mem-router", from: "memory", to: "router" },
  { id: "e-router-arb", from: "router", to: "arbiter", dashed: true },
  { id: "e-arb-router", from: "arbiter", to: "router", dashed: true },
  { id: "e-router-fsm", from: "router", to: "fsm" },
  { id: "e-router-live", from: "router", to: "live" },
  { id: "e-router-rag", from: "router", to: "rag" },
  { id: "e-fsm-dst", from: "fsm", to: "dst" },
  { id: "e-fsm-gate", from: "fsm", to: "gate", dashed: true },
  { id: "e-dst-gate", from: "dst", to: "gate", dashed: true },
  { id: "e-gate-staffmail", from: "gate", to: "staffmail" },
  { id: "e-staffmail-sheets", from: "staffmail", to: "sheets" },
  { id: "e-gate-sheets", from: "gate", to: "sheets", dashed: true },
  { id: "e-gate-answer", from: "gate", to: "answer", dashed: true },
  { id: "e-dst-llm", from: "dst", to: "llm", dashed: true },
  { id: "e-dst-answer", from: "dst", to: "answer", dashed: true },
  { id: "e-fsm-llm", from: "fsm", to: "llm" },
  { id: "e-fsm-answer", from: "fsm", to: "answer", dashed: true },
  { id: "e-fsm-sheets", from: "fsm", to: "sheets", dashed: true },
  { id: "e-live-calendar", from: "live", to: "calendar" },
  { id: "e-live-hko", from: "live", to: "hko" },
  { id: "e-rag-dense", from: "rag", to: "dense" },
  { id: "e-rag-sparse", from: "rag", to: "sparse" },
  { id: "e-dense-fuse", from: "dense", to: "fuse" },
  { id: "e-sparse-fuse", from: "sparse", to: "fuse" },
  { id: "e-fuse-grade", from: "fuse", to: "grade" },
  { id: "e-grade-rewrite", from: "grade", to: "rewrite", dashed: true },
  { id: "e-grade-llm", from: "grade", to: "llm" },
  // Corrective loop: rewrite → same RAG hub (2nd iteration), not a 2nd KB
  { id: "e-rewrite-rag", from: "rewrite", to: "rag", dashed: true },
  { id: "e-rewrite-llm", from: "rewrite", to: "llm", dashed: true },
  { id: "e-live-llm", from: "live", to: "llm", dashed: true },
  { id: "e-llm-answer", from: "llm", to: "answer" },
  { id: "e-sheets-answer", from: "sheets", to: "answer" },
];

const WORLD = { minX: -620, minY: -28, maxX: 660, maxY: 1240 };
/** Default Fit: top of the spine (parent → router), not the whole flowchart. */
const FIT_FOCUS = { minX: -300, minY: -24, maxX: 300, maxY: 460 };
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

/** Dialogue FSM routes (sticky / commit tools) — exclusive branch from Router. */
const FSM_ROUTES = new Set([
  "booking_dialogue",
  "cancel_dialogue",
  "staff_escalation",
  "booking_tool",
]);
/** Live-info tools (hours / HKO) — distinct from FSM; may still KB-enrich. */
const LIVE_ROUTES = new Set([
  "opening_hours",
  "deterministic_opening_hours",
  "severe_weather",
]);

/**
 * Three-way cascade under Router:
 *   1) FSM dialogue  2) live tools  3) RAG
 * FSM is the only hard branch; live and RAG can both light on enrich turns.
 */
function pathKind(route, grounding) {
  const g = grounding?.mode;
  // "pure_rag" kept for older traces; new API emits "rag"
  if (g === "rag" || g === "pure_rag" || route === "rag" || route === "off_domain") {
    return "rag";
  }
  if (LIVE_ROUTES.has(route)) return "live";
  if (FSM_ROUTES.has(route) || route) return "fsm";
  return null;
}

/** Live arm: HKO/hours facts, register seats, weather feed, etc. */
function liveUsed(route, grounding, facts) {
  return Boolean(
    grounding?.live_doc ||
      facts ||
      LIVE_ROUTES.has(route) ||
      route === "booking_tool" ||
      route === "opening_hours"
  );
}

function hoursApisUsed(route, facts, sources) {
  if (LIVE_ROUTES.has(route) || facts) return true;
  const consulted = sources?.consulted || [];
  return consulted.some((s) =>
    /hours_calendar|hko_opendata|calendar|hko/i.test(`${s.id || ""} ${s.label || ""}`)
  );
}

function formatRouteScore(route, value, origin) {
  if (route === "rag" && (origin?.source === "default_floor" || Number(value) === 0.5)) {
    return "default";
  }
  if (value == null || Number.isNaN(Number(value))) return "—";
  return Number(value).toFixed(2);
}

/** Detector score groups for the Rules · scores inspector panel. */
const SCORE_GROUPS = [
  {
    id: "tools",
    title: "Tool / dialogue",
    blurb: "Booking, cancel, escalate",
    routes: [
      "staff_escalation",
      "cancel_dialogue",
      "booking_dialogue",
      "booking_tool",
    ],
  },
  {
    id: "live",
    title: "Live info",
    blurb: "Hours · typhoon / HKO",
    routes: [
      "severe_weather",
      "opening_hours",
      "deterministic_opening_hours",
    ],
  },
  {
    id: "rag",
    title: "RAG default",
    blurb: "Centre Markdown retrieve",
    routes: ["rag", "off_domain"],
  },
];

function scoreGroupForRoute(routeId) {
  const id = String(routeId || "");
  for (const g of SCORE_GROUPS) {
    if (g.routes.includes(id)) return g.id;
  }
  return "other";
}

function StageIO({ request, payload, defaultOpen = false }) {
  if (!request && !payload) return null;
  const asText = (v) =>
    typeof v === "string" ? v : JSON.stringify(v, null, 2);
  return (
    <div className="bn-stage-io">
      {request != null && request !== "" ? (
        <details className="bn-llm-part" open={defaultOpen}>
          <summary>Request (raw)</summary>
          <pre className="bn-pre bn-pre-tall">{asText(request)}</pre>
        </details>
      ) : null}
      {payload != null && payload !== "" ? (
        <details className="bn-llm-part" open={defaultOpen}>
          <summary>Payload (raw)</summary>
          <pre className="bn-pre bn-pre-tall">{asText(payload)}</pre>
        </details>
      ) : null}
    </div>
  );
}

/** Collapsed-by-default “how this hard rule decided” panel. */
function HardDecisionReveal({ title, summary, steps, rows, children }) {
  if (!rows?.length && !steps?.length && !children) return null;
  return (
    <details className="bn-reveal bn-reveal-compact">
      <summary>
        <span className="bn-reveal-title">{title || "How this was decided"}</span>
        {summary ? <span className="bn-reveal-summary">{summary}</span> : null}
      </summary>
      <div className="bn-reveal-body">
        {steps?.length ? (
          <ol className="bn-reveal-steps">
            {steps.map((s, i) => (
              <li key={`hd-step-${i}`}>{s}</li>
            ))}
          </ol>
        ) : null}
        {rows?.length ? (
          <ul className="bn-fact-list">
            {rows.map((r) => (
              <li key={r.key || r.label}>
                <span>{r.label}</span>
                <code>{r.value}</code>
              </li>
            ))}
          </ul>
        ) : null}
        {children}
      </div>
    </details>
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

/**
 * Port on the VISIBLE shape edge — not the rectangular hit-box.
 * ISO parallelogram / diamond / hexagon ports were floating when treated as rects.
 */
function nodeAnchor(id, side = "bottom") {
  const n = LAYOUT[id];
  if (!n) return { x: 0, y: 0 };
  const shape = n.shape || "process";
  const left = n.x - n.w / 2;
  const right = n.x + n.w / 2;
  const top = n.y - n.h / 2;
  const bottom = n.y + n.h / 2;

  // Diamond (decision) — vertices on mid-sides of the square bbox
  if (shape === "decision") {
    const inset = Math.min(n.w, n.h) * 0.08;
    if (side === "top") return { x: n.x, y: top + inset };
    if (side === "bottom") return { x: n.x, y: bottom - inset };
    if (side === "left") return { x: left + inset, y: n.y };
    if (side === "right") return { x: right - inset, y: n.y };
    if (side === "sw") return { x: n.x - n.w * 0.28, y: n.y + n.h * 0.28 };
    if (side === "se") return { x: n.x + n.w * 0.28, y: n.y + n.h * 0.28 };
    if (side === "center") return { x: n.x, y: n.y };
    return { x: n.x, y: bottom - inset };
  }

  // ISO parallelogram (io) — SVG points "16,3 98,3 84,53 2,53" in 100×56
  // Ports sit ON the drawn stroke (SVG insets), not the rectangular hit-box.
  if (shape === "io") {
    const tMid = 0.5;
    const leftVb = 16 - 14 * tMid; // 9
    const rightVb = 98 - 14 * tMid; // 91
    const xAt = (vb) => left + (vb / 100) * n.w;
    const topY = top + (3 / 56) * n.h;
    const botY = bottom - (3 / 56) * n.h;
    // Nudge tips 1px into the fill so they kiss the stroke, not float outside
    if (side === "left") return { x: xAt(leftVb) + 1.2, y: n.y };
    if (side === "right") return { x: xAt(rightVb) - 1.2, y: n.y };
    if (side === "top") return { x: n.x, y: topY + 1.2 };
    if (side === "bottom") return { x: n.x, y: botY - 1.2 };
    if (side === "sw") return { x: xAt(8), y: botY - 1.2 };
    if (side === "se") return { x: xAt(78), y: botY - 1.2 };
    if (side === "center") return { x: n.x, y: n.y };
    return { x: n.x, y: botY - 1.2 };
  }

  // Hexagon (llm) — flat left/right at vb x=6 / 94
  if (shape === "llm") {
    if (side === "left") return { x: left + 0.06 * n.w, y: n.y };
    if (side === "right") return { x: right - 0.06 * n.w, y: n.y };
    if (side === "top") return { x: n.x, y: top + 0.03 * n.h };
    if (side === "bottom") return { x: n.x, y: bottom - 0.03 * n.h };
    if (side === "sw") return { x: left + 0.06 * n.w, y: bottom - 0.18 * n.h };
    if (side === "se") return { x: right - 0.06 * n.w, y: bottom - 0.18 * n.h };
    if (side === "center") return { x: n.x, y: n.y };
    return { x: n.x, y: bottom - 0.03 * n.h };
  }

  // Cylinder — side ports at body mid; top on ellipse equator
  if (shape === "cylinder") {
    if (side === "top") return { x: n.x, y: top + 0.18 * n.h };
    if (side === "bottom") return { x: n.x, y: bottom };
    if (side === "left") return { x: left + 0.06 * n.w, y: n.y + 0.06 * n.h };
    if (side === "right") return { x: right - 0.06 * n.w, y: n.y + 0.06 * n.h };
    if (side === "sw") return { x: left + 0.1 * n.w, y: bottom };
    if (side === "se") return { x: right - 0.1 * n.w, y: bottom };
    if (side === "center") return { x: n.x, y: n.y };
    return { x: n.x, y: bottom };
  }

  // Process / rule / terminal — rectangle (rule has side rails but same outer box)
  if (side === "top") return { x: n.x, y: top };
  if (side === "left") return { x: left, y: n.y };
  if (side === "right") return { x: right, y: n.y };
  if (side === "sw") return { x: left + 0.18 * n.w, y: bottom };
  if (side === "se") return { x: right - 0.18 * n.w, y: bottom };
  if (side === "center") return { x: n.x, y: n.y };
  return { x: n.x, y: bottom };
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

/** Collapse near-duplicate polyline points; keep corners crisp. */
function dedupePts(pts, eps = 0.5) {
  const out = [];
  for (const p of pts) {
    const prev = out[out.length - 1];
    if (!prev || Math.hypot(p.x - prev.x, p.y - prev.y) > eps) out.push(p);
  }
  return out;
}

/**
 * Stroke ends at the arrow BASE; tip sits on the node port.
 * Extra inset covers lit stroke-width anti-alias so the tip stays crisp.
 */
function splitArrow(pts, size = ARROW_SIZE) {
  const raw = dedupePts(pts);
  if (raw.length < 2) return null;
  const tip = raw[raw.length - 1];
  const prev = raw[raw.length - 2];
  const dx = tip.x - prev.x;
  const dy = tip.y - prev.y;
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len;
  const uy = dy / len;
  // Tip on port; stroke stops at base, inset ~½ lit stroke so AA can't nub.
  const back = Math.min(size, Math.max(8, Math.min(len * 0.5, len - 1)));
  const strokeInset = 1.4;
  const base = {
    x: tip.x - ux * (back + strokeInset),
    y: tip.y - uy * (back + strokeInset),
  };
  const wing = { x: tip.x - ux * back, y: tip.y - uy * back };
  const linePts = dedupePts([...raw.slice(0, -1), base]);
  const px = -uy;
  const py = ux;
  const half = back * 0.58;
  const arrow = `M ${tip.x} ${tip.y} L ${wing.x + px * half} ${
    wing.y + py * half
  } L ${wing.x - px * half} ${wing.y - py * half} Z`;
  return { linePts, tip, base, arrow, ux, uy, pathLen: len };
}

function pathLength(pts) {
  let total = 0;
  for (let i = 1; i < (pts || []).length; i++) {
    total += Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
  }
  return total;
}

/** Label ~28% along path; skip short edges (labels collide with tips). */
function labelAlong(pts, frac = 0.28, nudge = 20) {
  if (!pts || pts.length < 2) return null;
  const total = pathLength(pts);
  if (total < 110) return null;
  let target = total * frac;
  for (let i = 1; i < pts.length; i++) {
    const d = Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
    if (target <= d || i === pts.length - 1) {
      const t = d ? Math.min(0.85, target / d) : 0;
      const x = pts[i - 1].x + (pts[i].x - pts[i - 1].x) * t;
      const y = pts[i - 1].y + (pts[i].y - pts[i - 1].y) * t;
      const dx = pts[i].x - pts[i - 1].x;
      const dy = pts[i].y - pts[i - 1].y;
      const len = Math.hypot(dx, dy) || 1;
      return { x: x - (dy / len) * nudge, y: y + (dx / len) * nudge - 4 };
    }
    target -= d;
  }
  return null;
}

function orthoVia(start, end, midX) {
  return [start, { x: midX, y: start.y }, { x: midX, y: end.y }, end];
}

function gutterAround(start, end, gutterX = LIVE_GUTTER_X) {
  return [
    start,
    { x: gutterX, y: start.y },
    { x: gutterX, y: end.y },
    end,
  ];
}

function edgeGeometry(fromId, toId) {
  const a = LAYOUT[fromId];
  const b = LAYOUT[toId];
  if (!a || !b) return null;

  let pts = null;

  if (fromId === "router" && toId === "fsm") {
    pts = [nodeAnchor("router", "sw"), nodeAnchor("fsm", "top")];
  } else if (fromId === "router" && toId === "live") {
    pts = [nodeAnchor("router", "bottom"), nodeAnchor("live", "top")];
  } else if (fromId === "router" && toId === "rag") {
    pts = [nodeAnchor("router", "se"), nodeAnchor("rag", "top")];
  } else if (fromId === "router" && toId === "arbiter") {
    pts = [nodeAnchor("router", "right"), nodeAnchor("arbiter", "left")];
  } else if (fromId === "arbiter" && toId === "router") {
    const s = nodeAnchor("arbiter", "bottom");
    const e = nodeAnchor("router", "right");
    pts = [s, { x: s.x, y: e.y }, e];
  } else if (fromId === "live" && toId === "calendar") {
    // Same row — single horizontal into calendar right port
    pts = [nodeAnchor("live", "left"), nodeAnchor("calendar", "right")];
  } else if (fromId === "live" && toId === "hko") {
    // Under Live — single vertical into HKO top
    pts = [nodeAnchor("live", "bottom"), nodeAnchor("hko", "top")];
  } else if (fromId === "fsm" && toId === "dst") {
    pts = [nodeAnchor("fsm", "bottom"), nodeAnchor("dst", "top")];
  } else if (fromId === "fsm" && toId === "gate") {
    pts = gutterAround(nodeAnchor("fsm", "left"), nodeAnchor("gate", "left"));
  } else if (fromId === "dst" && toId === "gate") {
    pts = [nodeAnchor("dst", "bottom"), nodeAnchor("gate", "top")];
  } else if (fromId === "gate" && toId === "staffmail") {
    pts = [nodeAnchor("gate", "bottom"), nodeAnchor("staffmail", "top")];
  } else if (fromId === "staffmail" && toId === "sheets") {
    pts = [nodeAnchor("staffmail", "bottom"), nodeAnchor("sheets", "top")];
  } else if (fromId === "gate" && toId === "sheets") {
    pts = gutterAround(nodeAnchor("gate", "left"), nodeAnchor("sheets", "left"));
  } else if (fromId === "gate" && toId === "answer") {
    pts = orthoVia(
      nodeAnchor("gate", "right"),
      nodeAnchor("answer", "left"),
      FSM_LLM_X
    );
  } else if (
    (fromId === "fsm" || fromId === "dst" || fromId === "live") &&
    toId === "llm"
  ) {
    if (fromId === "live") {
      // Skirt right of HKO, drop under pack band, then into LLM top
      const s = nodeAnchor("live", "right");
      const e = nodeAnchor("llm", "top");
      const hko = LAYOUT.hko;
      const clearX = Math.max(s.x + 36, hko.x + hko.w / 2 + 28);
      const dropY = e.y - 40;
      pts = [
        s,
        { x: clearX, y: s.y },
        { x: clearX, y: dropY },
        { x: e.x, y: dropY },
        e,
      ];
    } else {
      pts = orthoVia(
        nodeAnchor(fromId, "right"),
        nodeAnchor("llm", "left"),
        FSM_LLM_X
      );
    }
  } else if ((fromId === "fsm" || fromId === "dst") && toId === "answer") {
    pts = orthoVia(
      nodeAnchor(fromId, "right"),
      nodeAnchor("answer", "left"),
      FSM_LLM_X
    );
  } else if (fromId === "fsm" && toId === "sheets") {
    pts = gutterAround(
      nodeAnchor("fsm", "left"),
      nodeAnchor("sheets", "left")
    );
  } else if (fromId === "rag" && toId === "dense") {
    pts = [nodeAnchor("rag", "sw"), nodeAnchor("dense", "top")];
  } else if (fromId === "rag" && toId === "sparse") {
    pts = [nodeAnchor("rag", "se"), nodeAnchor("sparse", "top")];
  } else if (fromId === "dense" && toId === "fuse") {
    // Enter pack from the LEFT — no shared top rail with sparse
    const s = nodeAnchor("dense", "bottom");
    const e = nodeAnchor("fuse", "left");
    pts = [s, { x: s.x, y: e.y }, e];
  } else if (fromId === "sparse" && toId === "fuse") {
    // Enter pack from the RIGHT
    const s = nodeAnchor("sparse", "bottom");
    const e = nodeAnchor("fuse", "right");
    pts = [s, { x: s.x, y: e.y }, e];
  } else if (fromId === "fuse" && toId === "grade") {
    pts = [nodeAnchor("fuse", "bottom"), nodeAnchor("grade", "top")];
  } else if (fromId === "grade" && toId === "rewrite") {
    pts = [nodeAnchor("grade", "bottom"), nodeAnchor("rewrite", "top")];
  } else if (fromId === "grade" && toId === "llm") {
    const s = nodeAnchor("grade", "bottom");
    const e = nodeAnchor("llm", "right");
    pts = [s, { x: s.x, y: e.y }, e];
  } else if (fromId === "rewrite" && toId === "llm") {
    const s = nodeAnchor("rewrite", "bottom");
    const e = nodeAnchor("llm", "right");
    pts = [s, { x: s.x, y: e.y }, e];
  } else if (fromId === "rewrite" && toId === "rag") {
    // Far-right return — clear of Sparse (never under its cylinder)
    const s = nodeAnchor("rewrite", "right");
    const e = nodeAnchor("rag", "right");
    const sparseRight = LAYOUT.sparse.x + LAYOUT.sparse.w / 2;
    const loopX = Math.max(s.x, e.x, sparseRight) + 56;
    pts = [s, { x: loopX, y: s.y }, { x: loopX, y: e.y }, e];
  } else if (fromId === "llm" && toId === "answer") {
    pts = [nodeAnchor("llm", "bottom"), nodeAnchor("answer", "top")];
  } else if (fromId === "sheets" && toId === "answer") {
    const s = nodeAnchor("sheets", "right");
    const e = nodeAnchor("answer", "left");
    pts = [s, { x: e.x, y: s.y }, e];
  } else if (Math.abs(b.x - a.x) > 40 && Math.abs(b.y - a.y) < 30) {
    pts = [
      nodeAnchor(fromId, b.x < a.x ? "left" : "right"),
      nodeAnchor(toId, b.x < a.x ? "right" : "left"),
    ];
  } else {
    pts = [nodeAnchor(fromId, "bottom"), nodeAnchor(toId, "top")];
  }

  const split = splitArrow(pts, ARROW_SIZE);
  if (!split || split.linePts.length < 2) return null;
  return {
    linePts: split.linePts,
    tip: split.tip,
    arrow: split.arrow,
    labelAt: labelAlong(split.linePts, 0.28, 20),
    start: split.linePts[0],
    end: split.tip,
    fromId,
    toId,
  };
}

function ptsToPath(pts) {
  if (!pts || pts.length < 2) return "";
  return (
    `M ${pts[0].x} ${pts[0].y}` +
    pts.slice(1).map((p) => ` L ${p.x} ${p.y}`).join("")
  );
}

/**
 * Circuit-style hop: when a horizontal run crosses a vertical run,
 * the horizontal gets a small semicircle bridge over the junction.
 */
function withCrossingJumps(geometries, hopR = 7) {
  const geos = (geometries || []).filter(Boolean);
  const verticals = [];
  for (const g of geos) {
    const pts = g.linePts || [];
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1];
      const b = pts[i];
      if (Math.abs(a.x - b.x) < 0.75 && Math.abs(a.y - b.y) > hopR * 2) {
        verticals.push({
          x: a.x,
          y0: Math.min(a.y, b.y),
          y1: Math.max(a.y, b.y),
          edge: g,
        });
      }
    }
  }

  return geos.map((g) => {
    const pts = g.linePts || [];
    if (pts.length < 2) return { ...g, d: ptsToPath(pts) };

    const hops = [];
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1];
      const b = pts[i];
      if (!(Math.abs(a.y - b.y) < 0.75 && Math.abs(a.x - b.x) > hopR * 2)) continue;
      const y = a.y;
      const x0 = Math.min(a.x, b.x);
      const x1 = Math.max(a.x, b.x);
      for (const v of verticals) {
        if (v.edge === g) continue;
        if (v.x <= x0 + hopR || v.x >= x1 - hopR) continue;
        if (y <= v.y0 + hopR || y >= v.y1 - hopR) continue;
        const nearEnd =
          Math.hypot(v.x - a.x, y - a.y) < hopR * 1.5 ||
          Math.hypot(v.x - b.x, y - b.y) < hopR * 1.5;
        if (nearEnd) continue;
        hops.push({ i, x: v.x, y });
      }
    }
    if (!hops.length) return { ...g, d: ptsToPath(pts) };

    let d = `M ${pts[0].x} ${pts[0].y}`;
    let cursor = pts[0];
    for (let i = 1; i < pts.length; i++) {
      const b = pts[i];
      const dir = b.x >= cursor.x ? 1 : -1;
      const segHops = hops
        .filter((h) => h.i === i)
        .sort((p, q) => dir * (p.x - q.x));
      if (!segHops.length) {
        d += ` L ${b.x} ${b.y}`;
        cursor = b;
        continue;
      }
      for (const h of segHops) {
        const xEnter = h.x - dir * hopR;
        const xExit = h.x + dir * hopR;
        d += ` L ${xEnter} ${h.y}`;
        // Semicircle bridge (sweep so the bump sits above the wire)
        d += ` A ${hopR} ${hopR} 0 0 ${dir > 0 ? 1 : 0} ${xExit} ${h.y}`;
      }
      d += ` L ${b.x} ${b.y}`;
      cursor = b;
    }

    return { ...g, d, labelAt: labelAlong(pts, 0.28, 20) };
  });
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
  const cue = summary.cue || "";
  const headline = humanizeDetectorWhy(summary.headline || "");
  const cleanHeadline =
    cue && !/«/.test(headline)
      ? `Matched «${cue}»${summary.rule_label ? ` → ${summary.rule_label}` : "."}`
      : headline;
  const segments =
    (summary.segments || []).some((s) => s.hit)
      ? summary.segments
      : segmentsFromCue(summary.query || summary.annotated, cue);
  return (
    <div className="bn-match-summary">
      <div className="bn-match-summary-kicker">What fired</div>
      <p className="bn-match-headline">{cleanHeadline}</p>
      <QueryHighlight
        segments={segments}
        fallback={summary.query || summary.annotated}
        cue={cue}
      />
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

/** Look up a BM25 token's contribution (tokens are lowercased Latin / raw CJK). */
function tokenScoreLookup(tokenScores, surface) {
  if (!tokenScores || surface == null) return null;
  const raw = String(surface);
  if (Object.prototype.hasOwnProperty.call(tokenScores, raw)) {
    return Number(tokenScores[raw]);
  }
  const lower = raw.toLowerCase();
  if (Object.prototype.hasOwnProperty.call(tokenScores, lower)) {
    return Number(tokenScores[lower]);
  }
  return null;
}

function formatTokenContrib(token, value) {
  if (value == null || Number.isNaN(value)) return `${token}: no score`;
  const n = Number(value);
  const signed = n >= 0 ? `+${n.toFixed(4)}` : n.toFixed(4);
  return `${token} · BM25 ${signed}`;
}

/** Highlighted BM25 token with a fixed-position score tip (avoids overflow clip). */
function TokHit({ children, tip, contrib }) {
  const [pos, setPos] = useState(null);
  const hasScore = contrib != null && !Number.isNaN(contrib);
  return (
    <>
      <mark
        className={`bn-tok-hit${hasScore ? " has-score" : ""}`}
        title={tip}
        onMouseEnter={(e) => {
          if (!hasScore) return;
          const r = e.currentTarget.getBoundingClientRect();
          setPos({ x: r.left + r.width / 2, y: r.top });
        }}
        onMouseLeave={() => setPos(null)}
      >
        {children}
      </mark>
      {pos
        ? createPortal(
            <span
              className="bn-tok-tip"
              role="tooltip"
              style={{ left: pos.x, top: pos.y }}
            >
              {tip}
            </span>,
            document.body
          )
        : null}
    </>
  );
}

/** Highlight BM25 tokens as whole terms — never substring "i" inside "with". */
function highlightTokens(text, tokens, tokenScores = null) {
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
  return body.split(re).map((part, i) => {
    if (i % 2 !== 1) return <span key={`t-${i}`}>{part}</span>;
    const contrib = tokenScoreLookup(tokenScores, part);
    const tip = formatTokenContrib(String(part).toLowerCase(), contrib);
    return (
      <TokHit key={`tok-${i}`} tip={tip} contrib={contrib}>
        {part}
      </TokHit>
    );
  });
}

function ChannelHit({ hit, index, highlight = false }) {
  const [open, setOpen] = useState(false);
  const body = hit.text || hit.snippet || "";
  const tokens = hit.matched_tokens || hit.query_tokens || [];
  const tokenScores = hit.token_scores || null;
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
            Matched:{" "}
            {tokens.slice(0, 12).map((t, i) => {
              const c = tokenScoreLookup(tokenScores, t);
              const label =
                c != null && !Number.isNaN(c)
                  ? `${t} ${c >= 0 ? "+" : ""}${c.toFixed(2)}`
                  : t;
              return (
                <span key={`m-${t}-${i}`}>
                  {i ? " · " : ""}
                  <TokHit tip={formatTokenContrib(t, c)} contrib={c}>
                    {label}
                  </TokHit>
                </span>
              );
            })}
            {tokens.length > 12 ? "…" : ""}
          </p>
        ) : null}
        {!open && body ? (
          <p className="bn-hit-snip">
            {highlight
              ? highlightTokens(body.slice(0, 220), tokens, tokenScores)
              : body.slice(0, 220)}
          </p>
        ) : null}
        <span className="bn-hit-expand-hint">
          {open ? "Collapse chunk" : "Expand full chunk"}
        </span>
      </button>
      {open && body ? (
        <pre className="bn-hit-full">
          {highlight ? highlightTokens(body, tokens, tokenScores) : body}
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

/** Pass-1 vs Pass-2 views when CRAG rewrite retried the same retrieve arm. */
function retrievalPassViews(rag, inbound) {
  const retried = Boolean(rag?.query_rewrite?.retried);
  const kept = rag?.final_pass || rag?.query_rewrite?.kept || "pass1";
  const pass1Hybrid =
    rag?.hybrid_pass1 || (!retried ? rag?.hybrid : null) || null;
  const pass2Hybrid =
    rag?.hybrid_pass2 ||
    (retried && kept === "pass2" ? rag?.hybrid : null) ||
    null;
  const passes = [
    {
      id: "pass1",
      label: "Pass 1 · raw",
      query: rag?.pass1_query || inbound || "",
      hybrid: pass1Hybrid,
      hits: rag?.pass1_hits || [],
      kept: kept === "pass1",
    },
  ];
  if (retried) {
    passes.push({
      id: "pass2",
      label: "Pass 2 · rewrite",
      query:
        rag?.pass2_query ||
        rag?.query_rewrite?.pass2_query ||
        rag?.query_rewrite?.query ||
        "",
      hybrid: pass2Hybrid,
      hits: rag?.pass2_hits || [],
      kept: kept === "pass2",
    });
  }
  return { retried, kept, passes };
}

function RetrievalPassTabs({ rag, inbound, defaultPass, children }) {
  const { retried, kept, passes } = retrievalPassViews(rag, inbound);
  const initial =
    defaultPass ||
    (retried && kept === "pass2" ? "pass2" : "pass1");
  const [tab, setTab] = useState(initial);
  useEffect(() => {
    setTab(initial);
  }, [initial, rag?.pass1_query, rag?.pass2_query, rag?.query_rewrite?.retried]);

  const active = passes.find((p) => p.id === tab) || passes[0];
  if (!retried) {
    return children(active, { retried: false, kept, passes });
  }
  return (
    <div className="bn-pass-tabs-wrap">
      <div className="bn-pass-tabs" role="tablist" aria-label="Retrieve pass">
        {passes.map((p) => (
          <button
            key={p.id}
            type="button"
            role="tab"
            aria-selected={tab === p.id}
            className={`bn-pass-tab${tab === p.id ? " is-active" : ""}${
              p.kept ? " is-kept" : ""
            }`}
            onClick={() => setTab(p.id)}
          >
            {p.label}
            {p.kept ? <em> · kept</em> : null}
          </button>
        ))}
      </div>
      <p className="bn-note bn-pass-tab-note">
        Same Chroma + BM25 indexes · {tab === "pass2" ? "rewritten" : "raw"}{" "}
        query
        {active?.kept ? " · this pass was kept for generate" : ""}.
      </p>
      {children(active, { retried: true, kept, passes })}
    </div>
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
        Detectors first · cheap JSON for missing slots.
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

/** Soft-clean detector jargon parents should never see. */
function humanizeDetectorWhy(text) {
  let t = String(text || "").trim();
  if (!t) return "";
  t = t.replace(/\bvia strong\b/gi, "hours wording");
  t = t.replace(/\bvia holiday\b/gi, "holiday wording");
  t = t.replace(/\bvia weak\+open\b/gi, "date + open wording");
  t = t.replace(/\bdetect_opening_hours_intent[^\n·]*·?\s*/gi, "");
  t = t.replace(/\bfixed\s+0\.\d+\s*·?\s*/gi, "");
  t = t.replace(/\s{2,}/g, " ").replace(/\s·\s·/g, " · ").trim();
  return t;
}

function segmentsFromCue(query, cue) {
  const q = String(query || "");
  const c = String(cue || "");
  if (!q || !c) return [];
  const idx = q.toLowerCase().indexOf(c.toLowerCase());
  if (idx < 0) return [];
  const end = idx + c.length;
  const parts = [];
  if (idx > 0) parts.push({ text: q.slice(0, idx), hit: false });
  parts.push({ text: q.slice(idx, end), hit: true });
  if (end < q.length) parts.push({ text: q.slice(end), hit: false });
  return parts;
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
  const evidence = rule.match_evidence || {};
  const queryText = rule.query || evidence.full_query || "";
  const cue =
    evidence.cue ||
    rule.matched_span?.text ||
    rule.score_origin?.cue ||
    "";
  const segments =
    (evidence.segments || []).some((s) => s.hit)
      ? evidence.segments
      : segmentsFromCue(queryText, cue);
  const reason = humanizeDetectorWhy(
    rule.matched
      ? cue
        ? `Matched «${cue}».`
        : rule.why || rule.status_reason || rule.detail || ""
      : rule.why_not || rule.status_reason || rule.detail || ""
  );
  // Only keep checks that add information (skip failed overrides that didn't apply).
  const checks = (rule.checks || []).filter((c) => {
    if (!c) return false;
    if (rule.matched && !c.passed && /override|skipped/i.test(`${c.label} ${c.detail}`)) {
      return false;
    }
    return true;
  });

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
            {isWinner ? <em className="bn-rule-winner-tag"> · won</em> : null}
          </strong>
          {cue ? (
            <p className="bn-rule-cue-chip">
              matched <code>«{cue}»</code>
              {rule.score != null ? (
                <em> · {Number(rule.score).toFixed(2)}</em>
              ) : null}
            </p>
          ) : (
            <p className="bn-rule-reason">
              {reason || (rule.matched ? "Matched." : "Did not match.")}
              {rule.score != null && rule.matched ? (
                <em> · {Number(rule.score).toFixed(2)}</em>
              ) : null}
            </p>
          )}
          <span className="bn-hit-expand-hint">
            {open ? "Hide why" : "Show why"}
          </span>
        </div>
      </button>
      {open ? (
        <div className="bn-rule-why">
          <div className="bn-rule-block bn-rule-block-query">
            <span>Parent message</span>
            <QueryHighlight
              segments={segments}
              fallback={queryText || evidence.annotated}
              cue={cue}
            />
            {cue ? (
              <p className="bn-rule-cue-meta">
                Fired on <code>«{cue}»</code>
              </p>
            ) : rule.matched ? (
              <p className="bn-rule-cue-meta">
                {reason || "Matched from session / multi-signal check (no single word)."}
              </p>
            ) : (
              <p className="bn-rule-cue-meta">{reason || "No match."}</p>
            )}
          </div>
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
  hoursApisOn,
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

    case "lang": {
      const det = trace.language_detection || {};
      const counts = det.counts || {};
      const variant = det.variant_counts || {};
      const decision = det.decision || trace.language || "—";
      const hasCounts = Boolean(det.counts);
      const langRows = hasCounts
        ? [
            { key: "cjk", label: "CJK chars", value: String(counts.cjk ?? 0) },
            {
              key: "latin",
              label: "Latin letters",
              value: String(counts.latin ?? 0),
            },
            {
              key: "trad",
              label: "Trad-only distinctive",
              value: String(
                variant.trad_only ?? counts.trad_only ?? 0
              ),
            },
            {
              key: "simp",
              label: "Simp-only distinctive",
              value: String(
                variant.simp_only ?? counts.simp_only ?? 0
              ),
            },
            {
              key: "digits",
              label: "Digits",
              value: String(counts.digits ?? 0),
            },
            {
              key: "other",
              label: "Other printable",
              value: String(counts.other ?? 0),
            },
            {
              key: "ratio",
              label: "CJK ratio",
              value:
                det.cjk_ratio != null
                  ? `${det.cjk_ratio} (need ≥ ${det.cjk_threshold ?? 0.3})`
                  : "—",
            },
            ...(variant.winner
              ? [
                  {
                    key: "variant_winner",
                    label: "Variant majority",
                    value:
                      variant.winner === "tie"
                        ? `tie → ${variant.tie_break || "zh-HK"}`
                        : String(variant.winner),
                  },
                ]
              : []),
            {
              key: "method",
              label: "Method",
              value: det.method || "—",
            },
            {
              key: "final",
              label: "Final decision",
              value: decision,
            },
          ]
        : [
            {
              key: "method",
              label: "Method",
              value: det.method || "client / prior turn",
            },
            {
              key: "final",
              label: "Final decision",
              value: decision,
            },
          ];
      const langSteps = [
        "Match each printable char to a set: CJK · Latin · digits · other",
        "CJK ratio = CJK ÷ (CJK + Latin)",
        `If ratio ≥ ${det.cjk_threshold ?? 0.3}, majority of trad-only vs simp-only distinctive chars picks zh-HK / zh-CN (tie → zh-HK)`,
        "Otherwise → en (or Accept-Language / client override)",
      ];
      return (
        <>
          <p className="bn-rules-hint">
            Language from parent text · also filters KB retrieve (
            <code>en</code> / <code>zh-HK</code> / <code>zh-CN</code>).
          </p>
          <ul className="bn-fact-list">
            <li>
              <span>Language</span>
              <code>{decision}</code>
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
          <HardDecisionReveal
            title="Why this language"
            summary={decision}
            steps={hasCounts || det.method ? langSteps : null}
            rows={langRows}
          >
            {det.why ? <p className="bn-note">{det.why}</p> : null}
          </HardDecisionReveal>
        </>
      );
    }

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

    case "grade": {
      const qr = rag?.query_rewrite || null;
      const grade = rag?.retrieval_grade || null;
      const weakHits = rag?.pass1_hits || [];
      const ran = Boolean(
        hits.length || enrichMode || path === "rag" || qr || grade
      );
      if (!ran) {
        return <Skipped>Pass-1 retrieve did not run — nothing to grade.</Skipped>;
      }
      const g = grade?.grade || qr?.grade || "—";
      const why = grade?.why || qr?.grade_why || "";
      const top = weakHits[0];
      return (
        <>
          <p className="bn-rules-hint">
            Hard gate on pass-1 keep set · top-chunk retrieval score (no LLM).
            Weak → dashed edge to <strong>Rewrite query</strong>; enough → generate.
          </p>
          <ul className="bn-fact-list">
            <li>
              <span>Grade</span>
              <code>
                {g}
                {why ? ` · ${why}` : ""}
              </code>
            </li>
            <li>
              <span>Top chunk score</span>
              <code>
                {top?.score != null
                  ? Number(top.score).toFixed(3)
                  : top?.rerank?.score != null
                    ? Number(top.rerank.score).toFixed(3)
                    : "—"}
                {rankedPassLabel(top) ? ` · ${rankedPassLabel(top)}` : ""}
              </code>
            </li>
            <li>
              <span>Next</span>
              <code>
                {qr?.rewritten || qr?.retried
                  ? "rewrite + 2nd retrieve"
                  : "generate (no rewrite)"}
              </code>
            </li>
          </ul>
          <HardDecisionReveal
            title="Grade gate"
            summary={String(g)}
            steps={[
              "Read top kept chunk score after channel pack",
              "If below threshold → mark weak and allow rewrite",
              "If enough → skip rewrite LLM and go to generate",
            ]}
            rows={[
              { key: "grade", label: "Decision", value: String(g) },
              { key: "why", label: "Why", value: why || "—" },
              {
                key: "rewrite",
                label: "Rewrite LLM",
                value:
                  qr?.rewritten || qr?.retried
                    ? "will run"
                    : "inactive this turn",
              },
            ]}
          />
          {g !== "correct" && weakHits.length > 0 ? (
            <ChromaHitsPanel
              title="Pass-1 chunks graded not enough (inspect):"
              query={rag?.pass1_query || inbound}
              hits={weakHits}
              rag={rag}
            />
          ) : null}
        </>
      );
    }

    case "rewrite": {
      const qr = rag?.query_rewrite || null;
      if (!qr?.called && !qr?.rewritten && !qr?.retried) {
        return (
          <Skipped>
            Rewrite LLM inactive — grade said pass-1 was enough (or retrieve
            did not run).
          </Skipped>
        );
      }
      return (
        <>
          <p className="bn-rules-hint">
            Cheap LLM rewrite · only when grade is weak. Loops back to the same
            RAG indexes for one more retrieve (Pass 2).
          </p>
          <ul className="bn-fact-list">
            <li>
              <span>Rewritten</span>
              <code>{qr?.rewritten ? "yes" : qr?.reason || "called"}</code>
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
              rewrite: {
                rewritten: qr?.rewritten,
                query: qr?.query,
                reason: qr?.reason,
                retried: qr?.retried,
                kept: qr?.kept,
              },
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
          {llmCalls
            .filter((c) => c.role === "query_rewrite")
            .map((c, i) => (
              <LlmCallCard key={`qr-${i}`} call={c} defaultOpen />
            ))}
        </>
      );
    }

    case "rules":
    case "router": {
      const tools =
        routing?.tools ||
        routing?.debug?.tools ||
        trace?.tools ||
        {};
      const selectedTool = tools.selected_tool || {};
      const cascade = Array.isArray(tools.cascade) ? tools.cascade : [];
      const branchLabel =
        path === "fsm"
          ? routing?.dialogue_active || routing?.cancel_active
            ? `FSM dialogue · ${route || "fsm"}`
            : `FSM · ${route || tools.selected || "fsm"}`
          : path === "live"
            ? enrichMode
              ? `Live tools + KB enrich · ${route || "live"}`
              : `Live tools · ${route || tools.selected || "live"}`
            : path === "rag"
              ? "RAG only (no tool owned this turn)"
              : "—";
      const whyShort =
        path === "fsm"
          ? routing?.dialogue_active || routing?.cancel_active
            ? `Sticky ${route || "fsm"}`
            : `Dialogue / commit detector · ${tools.selected || route || "tool"}`
          : path === "live"
            ? enrichMode
              ? "Live calendar/HKO facts → LLM · KB enrich runs on the RAG arm"
              : "Live calendar / HKO decision"
            : path === "rag"
              ? "No tool ≥ threshold → retrieve → generate"
              : "—";
      return (
        <>
          <p className="bn-rules-hint">
            Cascade: FSM dialogue → live tools → RAG. Live facts go to the LLM;
            optional KB enrich is a parallel RAG arm. FSM is the only exclusive
            branch.
          </p>
          <MatchSummaryCard summary={matchSummary} />
          <ul className="bn-fact-list">
            <li>
              <span>Selected tool</span>
              <code>
                {tools.selected || route || "—"}
                {selectedTool.group ? ` · ${selectedTool.group}` : ""}
              </code>
            </li>
            <li>
              <span>Selector</span>
              <code>{tools.selector || "—"}</code>
            </li>
            <li>
              <span>This turn</span>
              <code>{branchLabel}</code>
            </li>
            <li>
              <span>Why</span>
              <code>{whyShort}</code>
            </li>
            {selectedTool.commit_gated ? (
              <li>
                <span>Commit gated</span>
                <code>yes · final check before write</code>
              </li>
            ) : null}
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
          {cascade.length > 0 ? (
            <HardDecisionReveal
              title="Why this tool won"
              summary={tools.selected || route || "cascade"}
              steps={[
                "L1 · hard detectors + sticky FSM (<1ms regex / state)",
                "L2 · action policy cheap LLM may promote RAG → tool (escalate / book / cancel / hours)",
                "L3 · selected tool runs; live facts go to DashScope, not into Channel pack",
              ]}
            >
              <ol className="bn-score-ledger">
                {cascade.map((layer) => (
                  <li
                    key={layer.id || layer.label}
                    className={`bn-led-${layer.status === "ran" ? "award" : "note"}`}
                  >
                    <strong>{layer.id || "·"}</strong>
                    <span>
                      {layer.label}
                      {layer.status ? ` · ${layer.status}` : ""}
                      {layer.detail ? ` — ${layer.detail}` : ""}
                    </span>
                  </li>
                ))}
              </ol>
            </HardDecisionReveal>
          ) : null}
          {(trace?.data_sources?.consulted || []).length > 0 ? (
            <p className="bn-note">
              Sources used this turn → open <strong>Live tools</strong> /
              calendar / HKO, or <strong>Channel pack · ranked</strong> (KB).
            </p>
          ) : null}
          {(() => {
            const scoreRows = Object.entries(routing?.scores || {}).sort(
              (a, b) => Number(b[1]) - Number(a[1])
            );
            const topRoute = scoreRows[0]?.[0] || null;
            const rulesByScore = [...rulesEvaluated].sort((a, b) => {
              const sa = Number(
                routing?.scores?.[a.id] ?? a.score ?? -Infinity
              );
              const sb = Number(
                routing?.scores?.[b.id] ?? b.score ?? -Infinity
              );
              return sb - sa;
            });
            const groupedScores = SCORE_GROUPS.map((g) => ({
              ...g,
              rows: scoreRows.filter(([k]) => g.routes.includes(k)),
            })).filter((g) => g.rows.length > 0);
            const otherScores = scoreRows.filter(
              ([k]) => scoreGroupForRoute(k) === "other"
            );
            if (otherScores.length) {
              groupedScores.push({
                id: "other",
                title: "Other",
                blurb: "Extra detector keys",
                rows: otherScores,
              });
            }
            const groupedRules = [
              ...SCORE_GROUPS.map((g) => ({
                ...g,
                rules: rulesByScore.filter((r) => g.routes.includes(r.id)),
              })),
              {
                id: "other",
                title: "Other",
                blurb: "",
                rules: rulesByScore.filter(
                  (r) => scoreGroupForRoute(r.id) === "other"
                ),
              },
            ].filter((g) => (g.rules || []).length > 0);
            return (
              <>
                <p className="bn-rules-hint">
                  <strong>Rules · scores</strong> · L1 ledger. Highest hard score
                  wins unless sticky FSM or{" "}
                  <strong>L2 Action policy</strong> (cheap LLM) promotes. A ★
                  with source action_policy is not a regex hit — open the L2
                  hexagon on the board.
                </p>
                {groupedScores.length > 0 ? (
                  groupedScores.map((g) => (
                    <div key={`score-g-${g.id}`} className="bn-score-group">
                      <p className="bn-rules-hint">
                        <strong>{g.title}</strong>
                        {g.blurb ? ` — ${g.blurb}` : ""}
                      </p>
                      <ul className="bn-fact-list">
                        {g.rows.map(([k, v]) => {
                          const origin =
                            routing.score_origins?.[k] ||
                            scoreLedger
                              .filter((e) => e.route === k)
                              .slice(-1)[0];
                          const isTop = k === topRoute;
                          return (
                            <li key={k}>
                              <span>
                                {isTop ? "★ " : ""}
                                {k}
                                {origin?.source ? (
                                  <em className="bn-score-src">
                                    {" "}
                                    · {origin.source}
                                  </em>
                                ) : null}
                              </span>
                              <code>
                                {formatRouteScore(k, v, origin)}
                              </code>
                            </li>
                          );
                        })}
                      </ul>
                    </div>
                  ))
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
                {groupedRules.length > 0 ? (
                  groupedRules.map((g) => (
                    <div key={`rule-g-${g.id}`} className="bn-score-group">
                      <p className="bn-rules-hint">
                        <strong>{g.title}</strong> · match evidence
                      </p>
                      <ul className="bn-rule-list">
                        {g.rules.map((r) => (
                          <RuleEvidence
                            key={r.id}
                            rule={r}
                            isWinner={
                              r.id === winningRuleId && Boolean(r.matched)
                            }
                          />
                        ))}
                      </ul>
                    </div>
                  ))
                ) : (
                  <Skipped>No rule trace on this turn.</Skipped>
                )}
              </>
            );
          })()}
        </>
      );
    }

    case "arbiter": {
      const ap = routing?.debug?.action_policy || {};
      const apOverride = routing?.debug?.action_policy_override || null;
      const apCalls = llmCalls.filter(
        (c) => c.role === "action_policy" || c.role === "commit_audit"
      );
      const arbCalls = llmCalls.filter((c) => c.role === "intent_arbiter");
      const l2Ran = Boolean(ap?.called || arbiter?.called);
      if (!l2Ran) {
        return (
          <>
            <Skipped>
              L2 cheap LLM idle
              {ap?.gate || ap?.reason
                ? ` — action policy ${ap.gate || ap.reason}`
                : arbiter?.reason
                  ? ` — ${arbiter.reason}`
                  : " · hard detectors / sticky were enough"}
              .
            </Skipped>
            <HardDecisionReveal
              title="When L2 runs"
              summary="skipped"
              steps={[
                "L1 hard detectors + sticky FSM score first (<1ms)",
                "Action policy (cheap LLM) classifies long-tail / soft escalate·book·cancel when RAG would otherwise win",
                "Intent arbiter only for close / ambiguous hard-detector races",
              ]}
              rows={[
                {
                  key: "ap",
                  label: "Action policy",
                  value: ap?.gate || ap?.reason || "not called",
                },
                {
                  key: "arb",
                  label: "Intent arbiter",
                  value: arbiter?.reason || "not called",
                },
              ]}
            />
          </>
        );
      }
      return (
        <>
          <p className="bn-rules-hint">
            L2 cheap LLM · not a hard regex win
            {ap?.called && apOverride
              ? ` · promoted ${apOverride.from || "rag"} → ${apOverride.to}`
              : ap?.called
                ? ` · act=${ap.act || "answer"} (kept RAG)`
                : ""}
            {arbiter?.called ? ` · arbiter ${arbiter.action || "decide"}` : ""}.
          </p>
          {ap?.called ? (
            <ul className="bn-fact-list">
              <li>
                <span>Action policy</span>
                <code>
                  {ap.act || "—"}
                  {ap.confidence != null
                    ? ` · ${Number(ap.confidence).toFixed(2)}`
                    : ""}
                </code>
              </li>
              <li>
                <span>Promote</span>
                <code>
                  {apOverride
                    ? `${apOverride.from || "rag"} → ${apOverride.to}`
                    : "no · stay on RAG floor"}
                </code>
              </li>
              {ap.model || ap.provider ? (
                <li>
                  <span>Model</span>
                  <code>{ap.model || ap.provider}</code>
                </li>
              ) : null}
              {ap.reason ? (
                <li>
                  <span>Why</span>
                  <code>{ap.reason}</code>
                </li>
              ) : null}
            </ul>
          ) : null}
          {apCalls.map((c, i) => (
            <LlmCallCard key={`ap-${i}`} call={c} defaultOpen />
          ))}
          {arbiter?.called ? (
            <>
              <ul className="bn-fact-list">
                <li>
                  <span>Intent arbiter</span>
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
          ) : null}
          {ap?.called && !apCalls.length ? (
            <StageIO
              request={{ route_before: apOverride?.from || route, scores: routing?.scores }}
              payload={{
                act: ap.act,
                confidence: ap.confidence,
                override: apOverride,
                reason: ap.reason || null,
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
            {path === "live"
              ? "FSM dialogue not taken — this turn used live tools."
              : "FSM path not taken (this turn used RAG)."}
          </Skipped>
        );
      }
      return (
        <>
          <p className="bn-rules-hint">
            Tool / sticky dialogue · <code>{route || "—"}</code>. Slots on DST ·
            gate on Final check.
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
          {(() => {
            const esc =
              trace?.escalation ||
              trace?.debug?.escalation ||
              routing?.debug?.escalation;
            if (!esc) return null;
            return (
              <HardDecisionReveal
                title="Why escalation"
                summary={esc.category || "staff"}
                steps={[
                  "Hard regex cascade: safety → angry → hard_conflict → ask_human",
                  "First match wins · fixed score 1.0 on the Rules cascade",
                ]}
                rows={[
                  {
                    key: "category",
                    label: "Category",
                    value: esc.category || "—",
                  },
                  {
                    key: "priority",
                    label: "Priority",
                    value: esc.priority || "—",
                  },
                  ...(esc.cue
                    ? [
                        {
                          key: "cue",
                          label: "Matched cue",
                          value: `«${esc.cue}»`,
                        },
                      ]
                    : []),
                  {
                    key: "summary",
                    label: "Staff summary",
                    value: esc.summary || "—",
                  },
                ]}
              />
            );
          })()}
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
            Final check · cheap JSON gate before book / cancel / escalate.
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
          <Skipped>No staff-email draft this turn.</Skipped>
        );
      }
      return (
        <>
          <p className="bn-rules-hint">Staff email draft for Mailbox.</p>
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
            DST · cheap slot JSON after regex (not parent-facing generate).
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

    case "rag": {
      const retrieveRan =
        path === "rag" ||
        enrichMode ||
        hits.length ||
        rag?.hybrid ||
        rag?.hybrid_pass1;
      if (!retrieveRan) {
        return <Skipped>Retrieve arm idle this turn.</Skipped>;
      }
      return (
        <>
          <p className="bn-rules-hint">
            {path === "live" || (enrichMode && path !== "rag")
              ? "KB enrich retrieve is active on the RAG arm · Dense ∥ Sparse → Channel pack (live facts go to the LLM, not into Pack)."
              : "Retrieve → generate · Dense ∥ Sparse → pack → grade."}
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
        </>
      );
    }

    case "live": {
      if (!(path === "live" || liveOn)) {
        return <Skipped>No live tools on this turn.</Skipped>;
      }
      const liveDoc =
        trace?.opening_hours?.live_doc ||
        grounding?.live_doc_preview ||
        "";
      return (
        <>
          <p className="bn-rules-hint">
            Live info hub · calendar left of Live · HKO under Live · facts →
            DashScope
            {enrichMode ? " · KB enrich is a parallel RAG arm" : ""}.
          </p>
          <ul className="bn-fact-list">
            <li>
              <span>Route</span>
              <code>{route || "—"}</code>
            </li>
            <li>
              <span>Decision</span>
              <code>{trace?.opening_hours?.decision || "—"}</code>
            </li>
            <li>
              <span>LIVE doc</span>
              <code>{liveDoc ? `${liveDoc.length} chars` : "empty"}</code>
            </li>
          </ul>
          {liveDoc ? (
            <pre className="bn-pre bn-pre-tall">{liveDoc}</pre>
          ) : (
            <p className="bn-note">
              No privileged live summary attached this turn.
            </p>
          )}
          {(facts?.checks || []).length > 0 ? (
            <HardDecisionReveal
              title="Why open / closed"
              summary={trace?.opening_hours?.decision || "checks"}
              steps={[
                "Resolve the asked date (or today)",
                "Run holiday · Sunday · HKO same-day checks in order",
                "First hard closure wins; else use open–close window",
              ]}
              rows={(facts.checks || []).map((c) => ({
                key: c.id || c.label,
                label: c.label || c.id,
                value: [
                  c.checked === false ? "skipped" : c.passed ? "yes" : "no",
                  c.detail || "",
                ]
                  .filter(Boolean)
                  .join(" · "),
              }))}
            />
          ) : null}
        </>
      );
    }

    case "calendar": {
      if (!hoursApisOn && !facts) {
        return <Skipped>HK calendar not consulted this turn.</Skipped>;
      }
      const holCheck = (facts?.checks || []).find(
        (c) => c.id === "public_holiday"
      );
      const sunCheck = (facts?.checks || []).find((c) => c.id === "sunday");
      return (
        <>
          <p className="bn-rules-hint">
            Hong Kong public-holiday calendar + Sunday closed rule.
          </p>
          <ul className="bn-fact-list">
            <li>
              <span>Resolved</span>
              <code>
                {facts?.datetime
                  ? String(facts.datetime).replace(/\.\d+/, "").replace("T", " ")
                  : "—"}
              </code>
            </li>
            <li>
              <span>Holiday</span>
              <code>
                {facts?.is_holiday
                  ? facts.holiday_name || "yes"
                  : "no"}
              </code>
            </li>
            <li>
              <span>Sunday</span>
              <code>{facts?.is_sunday ? "closed" : "no"}</code>
            </li>
            <li>
              <span>Window</span>
              <code>
                {facts?.open_time && facts?.close_time
                  ? `${facts.open_time}–${facts.close_time}`
                  : facts?.is_sunday
                    ? "closed (Sunday)"
                    : "—"}
              </code>
            </li>
          </ul>
          <HardDecisionReveal
            title="Calendar checks"
            summary={
              facts?.is_holiday
                ? "holiday"
                : facts?.is_sunday
                  ? "Sunday"
                  : "open day"
            }
            rows={[
              holCheck
                ? {
                    key: "hol",
                    label: holCheck.label,
                    value: `${holCheck.passed ? "yes" : "no"} · ${holCheck.detail || ""}`,
                  }
                : {
                    key: "hol",
                    label: "Public holiday",
                    value: facts?.is_holiday ? "yes" : "no",
                  },
              sunCheck
                ? {
                    key: "sun",
                    label: sunCheck.label,
                    value: `${sunCheck.passed ? "yes" : "no"} · ${sunCheck.detail || ""}`,
                  }
                : {
                    key: "sun",
                    label: "Sunday",
                    value: facts?.is_sunday ? "yes" : "no",
                  },
            ]}
          />
        </>
      );
    }

    case "hko": {
      if (!hoursApisOn && !facts) {
        return <Skipped>HKO Open Data not consulted this turn.</Skipped>;
      }
      const hkoCheck = (facts?.checks || []).find(
        (c) => c.id === "hko_weather"
      );
      const checked =
        Boolean(facts?.weather_hint) ||
        facts?.parse_debug?.weather_checked ||
        facts?.parse_debug?.weather_resolved_same_day ||
        hkoCheck?.checked;
      return (
        <>
          <p className="bn-rules-hint">
            Live HKO warning feed · same-day only (future dates skip).
          </p>
          <ul className="bn-fact-list">
            <li>
              <span>Status</span>
              <code>
                {facts?.weather_hint
                  ? `signal · ${facts.weather_hint}`
                  : checked
                    ? "checked · clear"
                    : "skipped"}
              </code>
            </li>
            <li>
              <span>Same-day</span>
              <code>
                {facts?.parse_debug?.weather_resolved_same_day
                  ? "yes"
                  : facts?.parse_debug?.weather_same_day_only
                    ? "no · skipped"
                    : "—"}
              </code>
            </li>
          </ul>
          {hkoCheck ? (
            <HardDecisionReveal
              title="Why this HKO result"
              summary={
                facts?.weather_hint
                  ? "signal"
                  : checked
                    ? "clear"
                    : "skipped"
              }
              rows={[
                {
                  key: "hko",
                  label: hkoCheck.label,
                  value: [
                    hkoCheck.checked === false
                      ? "skipped"
                      : hkoCheck.passed
                        ? "yes"
                        : "no",
                    hkoCheck.detail || "",
                  ]
                    .filter(Boolean)
                    .join(" · "),
                },
              ]}
            />
          ) : null}
        </>
      );
    }

    case "dense": {
      const ran =
        path === "rag" ||
        enrichMode ||
        rag?.hybrid ||
        rag?.hybrid_pass1 ||
        hits.length;
      if (!ran) return <Skipped>Dense retrieve did not run.</Skipped>;
      return (
        <RetrievalPassTabs rag={rag} inbound={inbound}>
          {(pass) => {
            const hy = pass.hybrid || {};
            return (
              <ChannelPoolPanel
                title="Dense · Chroma (embeddings)"
                query={pass.query}
                rows={hy.dense_top || []}
                meta={{
                  Pool: hy.dense_count ?? "—",
                  Index: rag?.index_size ?? "—",
                  Mode: rag?.embedding_mode || "—",
                  Pass: pass.id,
                }}
                emptyHint={
                  !pass.hybrid && rag?.query_rewrite?.retried
                    ? "Pass-1 dense pool not in this payload — redeploy API for hybrid_pass1/2."
                    : hy.mode && !isChannelPack(hy)
                      ? `Dense-only / fallback: ${hy.reason || hy.mode}`
                      : "No dense pool rows in debug payload."
                }
              />
            );
          }}
        </RetrievalPassTabs>
      );
    }

    case "sparse": {
      const ran =
        path === "rag" ||
        enrichMode ||
        rag?.hybrid ||
        rag?.hybrid_pass1 ||
        hits.length;
      if (!ran) return <Skipped>Sparse retrieve did not run.</Skipped>;
      return (
        <RetrievalPassTabs rag={rag} inbound={inbound}>
          {(pass) => {
            const hy = pass.hybrid || {};
            return (
              <ChannelPoolPanel
                title="Sparse · BM25 (keyword / CJK tokens)"
                query={pass.query}
                rows={hy.sparse_top || []}
                meta={{
                  Pool: hy.sparse_count ?? "—",
                  Index: rag?.sparse_index_size ?? "—",
                  Pass: pass.id,
                }}
                highlightTokens
                emptyHint={
                  !pass.hybrid && rag?.query_rewrite?.retried
                    ? "Pass-1 BM25 pool not in this payload — redeploy API for hybrid_pass1/2."
                    : hy.mode && !isChannelPack(hy)
                      ? `BM25 skipped: ${hy.reason || hy.mode}`
                      : "No BM25 pool rows in debug payload."
                }
              />
            );
          }}
        </RetrievalPassTabs>
      );
    }

    case "chroma":
    case "fuse": {
      const ran =
        path === "rag" ||
        enrichMode ||
        rag?.hybrid ||
        rag?.hybrid_pass1 ||
        hits.length;
      if (!ran) return <Skipped>Channel pack did not run.</Skipped>;
      return (
        <RetrievalPassTabs rag={rag} inbound={inbound}>
          {(pass) => {
            const hy = pass.hybrid || {};
            if (hy.mode && !isChannelPack(hy)) {
              return (
                <>
                  <p className="bn-rules-hint">
                    Single-channel fallback this pass.
                  </p>
                  <ul className="bn-fact-list">
                    <li>
                      <span>Query</span>
                      <code>{pass.query || "—"}</code>
                    </li>
                    <li>
                      <span>Mode</span>
                      <code>{hy.mode || "—"}</code>
                    </li>
                    <li>
                      <span>Reason</span>
                      <code>{hy.reason || "—"}</code>
                    </li>
                  </ul>
                  <ChromaHitsPanel
                    title={`${pass.label} · ranked keep`}
                    query={pass.query}
                    hits={pass.hits}
                    rag={rag}
                    hybrid={hy}
                    emptyHint={`${pass.label} returned no chunks.`}
                  />
                </>
              );
            }
            const preview = hy.packed_preview || hy.fused_preview || [];
            const keepHits = pass.hits?.length ? pass.hits : [];
            const keepRows =
              preview.length > 0
                ? preview.slice(0, 8).map((row) => ({
                    key: `fp-${pass.id}-${row.rank}-${row.source}`,
                    rank: row.rank,
                    title: row.title || row.source || "chunk",
                    why: [
                      row.slot || null,
                      row.dense_rank != null
                        ? `dense #${row.dense_rank}`
                        : null,
                      row.sparse_rank != null
                        ? `BM25 #${row.sparse_rank}`
                        : null,
                    ]
                      .filter(Boolean)
                      .join(" · "),
                  }))
                : keepHits.map((h, i) => ({
                    key: `kept-${pass.id}-${h.source}-${i}`,
                    rank: h.rank ?? i + 1,
                    title: h.title || h.source || "hit",
                    why: [
                      h.pack?.slot || h.rerank?.why || "packed",
                      h.fusion?.dense_rank != null
                        ? `dense #${h.fusion.dense_rank}`
                        : null,
                      h.fusion?.sparse_rank != null
                        ? `BM25 #${h.fusion.sparse_rank}`
                        : null,
                    ]
                      .filter(Boolean)
                      .join(" · "),
                  }));
            const keepK =
              rag?.top_k ?? hy.keep_k ?? (keepHits.length || 5);
            const denseN = hy.dense_n ?? 3;
            const denseKept = hy.dense_kept ?? 0;
            const sparseAdded = hy.sparse_added ?? 0;
            const sparsePool = hy.sparse_count ?? 0;
            const dupes = hy.skipped_dupes ?? 0;
            let packWhy = "";
            if (sparseAdded === 0 && denseKept >= Math.min(denseN, keepK)) {
              packWhy = `Why only dense: keep=${keepK} and dense already filled ranks 1–${Math.min(
                denseN,
                keepK
              )} from its pool of ${hy.dense_count ?? "—"}. BM25 still retrieved ${sparsePool} candidates${
                dupes ? ` (${dupes} already in the dense set)` : ""
              }, but no empty slots remained to fill.`;
            } else if (sparseAdded > 0) {
              packWhy = `Dense took ranks 1–${denseKept}; BM25 filled ${sparseAdded} remaining slot${
                sparseAdded === 1 ? "" : "s"
              } with non-duplicate keyword hits.`;
            }
            return (
              <>
                <p className="bn-rules-hint">
                  <strong>Channel pack · ranked</strong> · one process: pack
                  keep={keepK} (dense top {denseN}, then BM25) → ranked keep
                  set{pass.kept ? " (kept for generate)" : ""}.
                </p>
                <ul className="bn-fact-list">
                  <li>
                    <span>Query</span>
                    <code>{pass.query || "—"}</code>
                  </li>
                  <li>
                    <span>Dense kept</span>
                    <code>
                      {denseKept} of top {denseN} (pool{" "}
                      {hy.dense_count ?? "—"})
                    </code>
                  </li>
                  <li>
                    <span>BM25 added</span>
                    <code>
                      {sparseAdded} (pool {sparsePool}
                      {dupes ? ` · ${dupes} dupes skipped` : ""})
                    </code>
                  </li>
                  <li>
                    <span>In pack</span>
                    <code>
                      both {hy.both ?? 0} · dense-only {hy.dense_only ?? 0} ·
                      BM25-only {hy.sparse_only ?? 0}
                    </code>
                  </li>
                </ul>
                {packWhy ? <p className="bn-note">{packWhy}</p> : null}
                {keepRows.length ? (
                  <>
                    <p className="bn-rules-hint">
                      Keep order ({pass.label})
                    </p>
                    <ul className="bn-fact-list">
                      {keepRows.map((row) => (
                        <li key={row.key}>
                          <span>
                            #{row.rank} {row.title}
                          </span>
                          <code>{row.why || "packed"}</code>
                        </li>
                      ))}
                    </ul>
                  </>
                ) : !pass.hybrid ? (
                  <Skipped>
                    Channel-pack preview for this pass not in payload.
                  </Skipped>
                ) : null}
                <ChromaHitsPanel
                  title={
                    enrichMode
                      ? `${pass.label} · ranked enrich hits`
                      : `${pass.label} · ranked keep set`
                  }
                  query={pass.query}
                  hits={pass.hits}
                  rag={rag}
                  hybrid={pass.hybrid || rag?.hybrid}
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
                  emptyHint={`${pass.label} returned no chunks.`}
                />
              </>
            );
          }}
        </RetrievalPassTabs>
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
          <p className="bn-rules-hint">Parent-facing generate · DashScope.</p>
          {genCalls.length === 0 ? (
            <Skipped>
              {dstOnly
                ? "No parent-facing LLM — see DST for slot JSON."
                : "No LLM call (template / deterministic)."}
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
          <Skipped>No staff-desk write this turn.</Skipped>
        );
      }
      return (
        <>
          <p className="bn-rules-hint">SheetPro / Mailbox / booking writes.</p>
          <ul className="bn-fact-list">
            {adminTodo ? (
              <li>
                <span>SheetPro</span>
                <code>{adminTodo.title || adminTodo.id || "todo written"}</code>
              </li>
            ) : null}
            {staffMessage ? (
              <li>
                <span>Mailbox</span>
                <code>
                  {staffMessage.category || "draft"}
                  {staffMessage.priority ? ` · ${staffMessage.priority}` : ""}
                </code>
              </li>
            ) : null}
            {trace?.booking?.booking ? (
              <li>
                <span>Booking</span>
                <code>{trace.booking.stage || "booked"}</code>
              </li>
            ) : null}
            {trace?.cancel?.cancelled || trace?.cancel?.stage === "cancelled" ? (
              <li>
                <span>Cancel</span>
                <code>cancelled</code>
              </li>
            ) : null}
          </ul>
        </>
      );
    }

    case "answer":
      return (
        <>
          <p className="bn-rules-hint">
            Parent-facing reply (sanitized). Model I/O on LLM / DST nodes.
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
      : shape === "io"
        ? "rgba(10, 126, 164, 0.7)"
        : shape === "rule"
          ? "rgba(180, 90, 90, 0.65)"
          : shape === "llm"
            ? "rgba(120, 90, 180, 0.65)"
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
      {/* ISO 5807 parallelogram — input/output / external tool or API */}
      {shape === "io" ? (
        <svg
          className="bn-shape-svg"
          viewBox="0 0 100 56"
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          <polygon
            points="16,3 98,3 84,53 2,53"
            fill={fill}
            stroke={stroke}
            strokeWidth="2.2"
          />
        </svg>
      ) : null}
      {/* ISO 5807 predefined process — hard-coded rule / subroutine */}
      {shape === "rule" ? (
        <svg
          className="bn-shape-svg"
          viewBox="0 0 100 56"
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          <rect
            x="6"
            y="3"
            width="88"
            height="50"
            rx="2"
            fill={fill}
            stroke={stroke}
            strokeWidth="2.2"
          />
          <line
            x1="14"
            y1="3"
            x2="14"
            y2="53"
            stroke={stroke}
            strokeWidth="2"
          />
          <line
            x1="86"
            y1="3"
            x2="86"
            y2="53"
            stroke={stroke}
            strokeWidth="2"
          />
        </svg>
      ) : null}
      {/* Hexagon — preparation / LLM call (distinct from process + store) */}
      {shape === "llm" ? (
        <svg
          className="bn-shape-svg"
          viewBox="0 0 100 70"
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          <polygon
            points="50,2 94,18 94,52 50,68 6,52 6,18"
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
  /** Active pointers for Maps-style pan + pinch (pointerId → {x,y}). */
  const pointersRef = useRef(new Map());
  const pinchRef = useRef(null);
  const closeBtnRef = useRef(null);
  const fittedRef = useRef(false);
  const zoomRef = useRef(zoom);
  const panRef = useRef(pan);
  /** User panned/zoomed — pending auto-fit must not overwrite their camera. */
  const userCameraRef = useRef(false);

  const markUserCamera = () => {
    userCameraRef.current = true;
  };

  useEffect(() => {
    zoomRef.current = zoom;
  }, [zoom]);
  useEffect(() => {
    panRef.current = pan;
  }, [pan]);

  const route = trace?.route;
  const grounding = trace?.grounding;
  const routing = trace?.routing;
  const llmCalls = Array.isArray(trace?.llm_calls)
    ? trace.llm_calls
    : EMPTY_ARR;
  const history = trace?.history;
  const rag = trace?.rag;
  const facts = trace?.opening_hours?.facts;
  const enrichMode = rag?.mode === "grounded_enrich" || grounding?.used_rag;
  const hits =
    route === "rag" || enrichMode
      ? Array.isArray(rag?.hits)
        ? rag.hits
        : EMPTY_ARR
      : EMPTY_ARR;
  const rulesEvaluatedRaw = Array.isArray(routing?.rules_evaluated)
    ? routing.rules_evaluated
    : Array.isArray(routing?.debug?.rules_evaluated)
      ? routing.debug.rules_evaluated
      : EMPTY_ARR;
  const hoursDebug =
    routing?.debug?.hours_debug ||
    routing?.hours_debug ||
    trace?.debug?.hours ||
    null;
  const inboundForRules =
    trace?.inbound_message ||
    parentMessage ||
    routing?.match_summary?.query ||
    routing?.debug?.match_summary?.query ||
    "";
  // Backfill hours cue from detector spans / regex hits when evidence is empty.
  const rulesEvaluated = useMemo(() => {
    return (rulesEvaluatedRaw || []).map((rule) => {
      if (rule?.id !== "opening_hours") return rule;
      const existing =
        rule.match_evidence?.cue || rule.matched_span?.text || "";
      if (existing) return rule;
      const span = hoursDebug?.matched_span;
      let cue = hoursDebug?.cue || span?.text || "";
      let matchedSpan = span || null;
      if (!cue && Array.isArray(hoursDebug?.strong_hits)) {
        let best = null;
        for (const pat of hoursDebug.strong_hits) {
          try {
            const m = String(inboundForRules || "").match(new RegExp(pat, "i"));
            if (m && (!best || m[0].length > best.text.length)) {
              best = {
                text: m[0],
                start: m.index,
                end: m.index + m[0].length,
              };
            }
          } catch {
            /* ignore bad pattern */
          }
        }
        if (best) {
          cue = best.text;
          matchedSpan = best;
        }
      }
      if (!cue) return rule;
      const segments = segmentsFromCue(inboundForRules, cue);
      return {
        ...rule,
        matched_span: matchedSpan || rule.matched_span,
        why: humanizeDetectorWhy(rule.why) || `Matched «${cue}».`,
        status_reason:
          humanizeDetectorWhy(rule.status_reason) || `Matched «${cue}».`,
        match_evidence: {
          ...(rule.match_evidence || {}),
          cue,
          kind: "substring",
          segments: segments.length
            ? segments
            : rule.match_evidence?.segments || [],
          explanation: `The parent message contains «${cue}», so the opening-hours tool wins.`,
          full_query: inboundForRules,
        },
        score_origin: rule.score_origin
          ? {
              ...rule.score_origin,
              cue,
              why: humanizeDetectorWhy(rule.score_origin.why) || `Matched «${cue}».`,
            }
          : rule.score_origin,
      };
    });
  }, [rulesEvaluatedRaw, hoursDebug, inboundForRules]);
  const winningRule =
    routing?.winning_rule || routing?.debug?.winning_rule || EMPTY_OBJ;
  const matchSummaryRaw =
    routing?.match_summary || routing?.debug?.match_summary || EMPTY_OBJ;
  const matchSummary = useMemo(() => {
    const hoursRule = rulesEvaluated.find((r) => r.id === "opening_hours");
    const cue =
      matchSummaryRaw.cue ||
      hoursRule?.match_evidence?.cue ||
      hoursDebug?.cue ||
      "";
    if (!cue && !matchSummaryRaw.headline) return matchSummaryRaw;
    const segments =
      (matchSummaryRaw.segments || []).some((s) => s.hit)
        ? matchSummaryRaw.segments
        : segmentsFromCue(
            matchSummaryRaw.query || inboundForRules,
            cue
          );
    return {
      ...matchSummaryRaw,
      cue: cue || matchSummaryRaw.cue,
      segments,
      headline: cue
        ? `Matched «${cue}» → ${
            matchSummaryRaw.rule_label ||
            hoursRule?.label ||
            "Opening hours"
          }.`
        : humanizeDetectorWhy(matchSummaryRaw.headline),
      rule_id:
        matchSummaryRaw.rule_id === "deterministic_opening_hours"
          ? "opening_hours"
          : matchSummaryRaw.rule_id,
    };
  }, [matchSummaryRaw, rulesEvaluated, hoursDebug, inboundForRules]);
  const arbiter = routing?.debug?.arbiter || EMPTY_OBJ;
  const actionPolicy = routing?.debug?.action_policy || EMPTY_OBJ;
  const actionPolicyOverride = routing?.debug?.action_policy_override || null;
  const l2CheapOn = Boolean(arbiter?.called || actionPolicy?.called);
  const winningRuleId =
    matchSummary?.rule_id ||
    (winningRule?.route === "deterministic_opening_hours"
      ? "opening_hours"
      : winningRule?.route) ||
    (route === "deterministic_opening_hours" ? "opening_hours" : route);
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
  const hoursApisOn = hoursApisUsed(
    route,
    facts,
    trace?.data_sources || routing?.tools?.data_sources
  );
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
        kind: "rules",
        shape: "rule",
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
        id: "arbiter",
        label: actionPolicy?.called
          ? actionPolicyOverride
            ? "Action policy · promote"
            : "Action policy"
          : arbiter?.called
            ? "Intent arbiter"
            : "L2 · cheap LLM",
        kind: "llm",
        shape: "llm",
        status: !hasTrace ? "idle" : l2CheapOn ? "done" : "skipped",
      },
      {
        id: "router",
        label:
          path === "fsm"
            ? "Router · FSM"
            : path === "live"
              ? enrichMode
                ? "Router · live+KB"
                : "Router · live"
              : path === "rag"
                ? "Router · RAG"
                : "Router · scores",
        kind: "router",
        shape: "decision",
        status: !hasTrace
          ? "idle"
          : rulesEvaluated.length || routing?.scores || path
            ? "done"
            : "done",
      },
      {
        id: "fsm",
        label: "FSM · dialogue",
        kind: "hard",
        shape: "process",
        status: !hasTrace ? "idle" : path === "fsm" ? "done" : "skipped",
      },
      {
        id: "dst",
        label: "DST · cheap JSON",
        kind: "llm",
        shape: "llm",
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
              : "skipped",
      },
      {
        id: "gate",
        label: "Final check",
        kind: "llm",
        shape: "llm",
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
        shape: "llm",
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
        id: "live",
        label: "Live tools",
        kind: "live",
        shape: "io",
        status: !hasTrace
          ? "idle"
          : path === "live" || liveOn
            ? "done"
            : "skipped",
      },
      {
        id: "calendar",
        label: "HK calendar",
        kind: "live",
        shape: "io",
        status: !hasTrace
          ? "idle"
          : hoursApisOn
            ? "done"
            : "skipped",
      },
      {
        id: "hko",
        label: "HKO Open Data",
        kind: "live",
        shape: "io",
        status: !hasTrace
          ? "idle"
          : hoursApisOn
            ? facts?.weather_hint
              ? "done"
              : facts?.parse_debug?.weather_checked ||
                  facts?.parse_debug?.weather_resolved_same_day ||
                  (facts?.checks || []).some(
                    (c) => c.id === "hko_weather" && c.checked
                  )
                ? "done"
                : "empty"
            : "skipped",
      },
      {
        id: "rag",
        label: "RAG",
        kind: "rag",
        shape: "process",
        // Live tools enrich the retrieve context — RAG still ran, not bypassed.
        status: !hasTrace
          ? "idle"
          : path === "rag" || enrichMode
            ? "done"
            : "skipped",
      },
      {
        id: "dense",
        label: "Dense · Chroma",
        kind: "store",
        shape: "cylinder",
        status: !hasTrace
          ? "idle"
          : !(path === "rag" || enrichMode)
            ? "skipped"
            : rag?.hybrid?.dense_count > 0 || (rag?.hybrid?.dense_top || []).length
              ? "done"
              : "empty",
      },
      {
        id: "sparse",
        label: "Sparse · BM25",
        kind: "store",
        shape: "cylinder",
        status: !hasTrace
          ? "idle"
          : !(path === "rag" || enrichMode)
            ? "skipped"
            : rag?.hybrid?.sparse_count > 0 ||
                (rag?.hybrid?.sparse_top || []).length
              ? "done"
              : isChannelPack(rag?.hybrid, rag)
                ? "empty"
                : "skipped",
      },
      {
        id: "fuse",
        label: rag?.query_rewrite?.retried
          ? "Channel pack · ranked"
          : "Channel pack · ranked",
        kind: "rag",
        shape: "process",
        status: !hasTrace
          ? "idle"
          : !(path === "rag" || enrichMode || hits.length || rag?.hybrid)
            ? "skipped"
            : isChannelPack(rag?.hybrid, rag) ||
                (rag?.pass1_hits || hits).length ||
                rag?.pass1_hit_count
              ? "done"
              : path === "rag" || enrichMode
                ? "empty"
                : "skipped",
      },
      {
        id: "grade",
        label: "Grade · top score",
        kind: "rules",
        shape: "rule",
        status: !hasTrace
          ? "idle"
          : !(path === "rag" || enrichMode || hits.length)
            ? "skipped"
            : rag?.retrieval_grade || rag?.query_rewrite?.grade
              ? "done"
              : path === "rag" || enrichMode
                ? "empty"
                : "skipped",
      },
      {
        id: "rewrite",
        label: "Rewrite query",
        kind: "llm",
        shape: "llm",
        status: !hasTrace
          ? "idle"
          : !(path === "rag" || enrichMode || hits.length)
            ? "skipped"
            : rag?.query_rewrite?.rewritten || rag?.query_rewrite?.retried
              ? "done"
              : rag?.query_rewrite?.called
                ? "empty"
                : "skipped",
      },
      {
        id: "llm",
        label: "LLM (DashScope)",
        kind: "llm",
        shape: "llm",
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
    actionPolicy,
    actionPolicyOverride,
    l2CheapOn,
    path,
    route,
    liveOn,
    hoursApisOn,
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
      "router",
      "answer",
    ]);
    if (history?.enabled === false) lit.delete("memory");
    if (l2CheapOn) lit.add("arbiter");
    // Retrieve arm: dense ∥ sparse → pack+rank → grade → (optional rewrite loop)
    const lightRetrieve = () => {
      lit.add("rag");
      lit.add("dense");
      lit.add("sparse");
      lit.add("fuse");
      lit.add("grade");
      if (rag?.query_rewrite?.rewritten || rag?.query_rewrite?.retried) {
        lit.add("rewrite");
      }
    };
    if (path === "fsm") {
      lit.add("fsm");
      if (
        (trace?.booking?.booking_dst || trace?.booking?.arbiter_booking)?.called ||
        trace?.cancel?.cancel_dst?.called
      ) {
        lit.add("dst");
      }
      if (
        finalCheck?.called ||
        llmCalls.some((c) => isFinalCheckCall(c)) ||
        route === "staff_escalation"
      ) {
        lit.add("gate");
      }
      if (
        staffMessage ||
        llmCalls.some((c) => isStaffEmailCall(c)) ||
        route === "staff_escalation" ||
        adminTodo
      ) {
        lit.add("staffmail");
      }
      if (
        adminTodo ||
        staffMessage ||
        route === "staff_escalation" ||
        trace?.booking?.stage === "booked" ||
        trace?.cancel?.stage === "cancelled"
      ) {
        lit.add("sheets");
      }
    } else if (path === "live") {
      lit.add("live");
      if (hoursApisOn) {
        lit.add("calendar");
        lit.add("hko");
      }
      // Optional KB enrich on the RAG arm — live facts still go Live → LLM.
      if (hits.length || enrichMode) lightRetrieve();
    } else if (path === "rag") {
      lightRetrieve();
      if (adminTodo) lit.add("sheets");
    }
    if (llmCalls.some((c) => isMainLlmCall(c))) lit.add("llm");
    // Grade enough with no rewrite still feeds generate
    if (
      lit.has("grade") &&
      !lit.has("rewrite") &&
      llmCalls.some((c) => isMainLlmCall(c))
    ) {
      lit.add("llm");
    }
    return lit;
  }, [
    trace,
    history?.enabled,
    l2CheapOn,
    path,
    route,
    liveOn,
    hoursApisOn,
    hits.length,
    enrichMode,
    llmCalls,
    adminTodo,
    staffMessage,
    finalCheck,
    rag?.query_rewrite?.retried,
    rag?.query_rewrite?.rewritten,
  ]);

  const litEdgeIds = useMemo(() => {
    if (!trace) return new Set();
    const lit = new Set();
    const on = (id) => activePath.has(id);
    const ok = (id) =>
      statusById[id] === "done" ||
      statusById[id] === "empty" ||
      (id === "memory" && on(id)) ||
      (id === "rewrite" && on(id)) ||
      (id === "grade" && on(id)) ||
      (id === "arbiter" && on(id)) ||
      (id === "gate" && on(id)) ||
      (id === "staffmail" && on(id)) ||
      (id === "sheets" && on(id));
    const link = (from, to, edgeId) => {
      if (on(from) && on(to) && ok(from) && ok(to)) lit.add(edgeId);
    };
    link("parent", "lang", "e-parent-lang");
    link("lang", "memory", "e-lang-mem");
    link("memory", "router", "e-mem-router");
    if (l2CheapOn) {
      link("router", "arbiter", "e-router-arb");
      link("arbiter", "router", "e-arb-router");
    }
    if (path === "fsm") link("router", "fsm", "e-router-fsm");
    if (path === "live") link("router", "live", "e-router-live");
    // Live/tool enrich still runs the retrieve arm — light RAG branch too.
    if (path === "rag" || enrichMode) link("router", "rag", "e-router-rag");
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
    // Live APIs fan-out under Live — no return arrows.
    if (on("live") && on("calendar")) link("live", "calendar", "e-live-calendar");
    if (on("live") && on("hko")) link("live", "hko", "e-live-hko");
    if (on("dst") && on("llm")) link("dst", "llm", "e-dst-llm");
    else if (on("fsm") && on("llm") && !on("rewrite"))
      link("fsm", "llm", "e-fsm-llm");
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
    if (on("fuse") && on("grade")) link("fuse", "grade", "e-fuse-grade");
    if (on("grade") && on("rewrite")) {
      link("grade", "rewrite", "e-grade-rewrite");
    }
    if (on("grade") && on("llm") && !on("rewrite")) {
      link("grade", "llm", "e-grade-llm");
    }
    if (on("rewrite") && on("rag") && rag?.query_rewrite?.retried) {
      // Corrective loop — second retrieve on the same RAG arm
      link("rewrite", "rag", "e-rewrite-rag");
    }
    if (on("rewrite") && on("llm")) {
      link("rewrite", "llm", "e-rewrite-llm");
    }
    // Live docs join at DashScope compose — not Channel pack (pack is KB enrich only).
    if (on("live") && on("llm")) {
      link("live", "llm", "e-live-llm");
    }
    if (on("llm")) link("llm", "answer", "e-llm-answer");
    if (on("sheets")) link("sheets", "answer", "e-sheets-answer");
    return lit;
  }, [
    trace,
    activePath,
    statusById,
    l2CheapOn,
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
      "e-mem-router": pick ? `→ ${pick}` : "",
      "e-router-arb": l2CheapOn
        ? actionPolicy?.called
          ? "L2 classify"
          : "L2 arbiter"
        : "",
      "e-arb-router": actionPolicy?.called
        ? actionPolicyOverride
          ? `${actionPolicyOverride.from || "rag"} → ${actionPolicyOverride.to}`
          : `${actionPolicy.act || "answer"} · keep`
        : arbiter?.called
          ? arbiter.action || "keep"
          : "",
      "e-router-fsm":
        path === "fsm"
          ? route === "staff_escalation"
            ? "escalate"
            : "dialogue"
          : "",
      "e-router-live": path === "live" ? "live info" : "",
      "e-router-rag":
        path === "rag" ? "RAG" : enrichMode ? "KB enrich" : "",
      "e-live-calendar": hoursApisOn ? "holiday" : "",
      "e-live-hko": hoursApisOn ? "weather" : "",
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
        hy?.dense_count != null ? `dense ${hy.dense_count}` : "dense",
      "e-sparse-fuse":
        hy?.sparse_count != null ? `BM25 ${hy.sparse_count}` : "BM25",
      "e-fuse-grade": isChannelPack(hy, rag)
        ? `pack · ${(pass1 || []).length || "—"} kept`
        : "ranked keep",
      "e-grade-rewrite":
        rag?.retrieval_grade?.grade || rag?.query_rewrite?.grade
          ? String(rag?.retrieval_grade?.grade || rag?.query_rewrite?.grade)
          : "weak?",
      "e-grade-llm": "enough · generate",
      "e-rewrite-rag": rag?.query_rewrite?.retried
        ? "2nd retrieve · same KB"
        : "",
      "e-rewrite-llm": rag?.query_rewrite?.retried
        ? `kept ${rag?.final_pass || rag?.query_rewrite?.kept || "pass2"}`
        : "",
      "e-live-llm":
        path === "live" || liveOn || enrichMode ? "facts → generate" : "",
    };
    return labels;
  }, [
    routing,
    winningRuleId,
    route,
    arbiter,
    actionPolicy,
    actionPolicyOverride,
    l2CheapOn,
    path,
    hits,
    rag,
    enrichMode,
    liveOn,
    hoursApisOn,
    trace,
    finalCheck,
  ]);

  const applyFitBox = useCallback((box, biasY = 0.45) => {
    const el = viewportRef.current;
    if (!el) return false;
    if (el.offsetParent === null && el.getClientRects().length === 0) return false;
    const { width, height } = el.getBoundingClientRect();
    if (width < 120 || height < 120) return false;
    const pad = 36;
    const gw = Math.max(120, box.maxX - box.minX);
    const gh = Math.max(120, box.maxY - box.minY);
    const nextZoom = Math.min(
      MAX_ZOOM,
      Math.max(
        MIN_ZOOM,
        Math.min((width - pad) / gw, (height - pad) / gh) * 0.92
      )
    );
    const cx = (box.minX + box.maxX) / 2;
    const cy = box.minY + (box.maxY - box.minY) * biasY;
    const rawPan = {
      x: width / 2 - cx * nextZoom,
      y: height / 2 - cy * nextZoom,
    };
    const nextPan = clampPan(rawPan, nextZoom, width, height);
    zoomRef.current = nextZoom;
    panRef.current = nextPan;
    setZoom(nextZoom);
    setPan(nextPan);
    return true;
  }, []);

  const fitView = useCallback(() => {
    return applyFitBox(FIT_FOCUS, 0.38);
  }, [applyFitBox]);

  /** Frame lit nodes so Live / Pack are never off-canvas after a turn. */
  const fitLitPath = useCallback(() => {
    const ids = [...activePath].filter(
      (id) =>
        statusById[id] === "done" ||
        statusById[id] === "empty" ||
        id === "memory"
    );
    if (ids.length < 2) return fitView();
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const id of ids) {
      const n = LAYOUT[id];
      if (!n) continue;
      minX = Math.min(minX, n.x - n.w / 2 - 24);
      maxX = Math.max(maxX, n.x + n.w / 2 + 24);
      minY = Math.min(minY, n.y - n.h / 2 - 24);
      maxY = Math.max(maxY, n.y + n.h / 2 + 24);
    }
    if (!Number.isFinite(minX)) return fitView();
    return applyFitBox({ minX, minY, maxX, maxY }, 0.45);
  }, [activePath, statusById, applyFitBox, fitView]);

  const fitLitPathRef = useRef(fitLitPath);
  const fitViewRef = useRef(fitView);
  useEffect(() => {
    fitLitPathRef.current = fitLitPath;
  }, [fitLitPath]);
  useEffect(() => {
    fitViewRef.current = fitView;
  }, [fitView]);

  useEffect(() => {
    setActiveId(null);
  }, [turnKey]);

  // Frame lit path once per answered turn. Skip if the user already moved the
  // camera (including during this 80ms delay) so zoom never snap-backs.
  useEffect(() => {
    if (!turnKey || turnKey === "idle") return undefined;
    userCameraRef.current = false;
    const t = window.setTimeout(() => {
      if (userCameraRef.current) return;
      fitLitPathRef.current();
    }, 80);
    return () => window.clearTimeout(t);
  }, [turnKey]);

  // Fit once when the canvas has a real size; never on hide/show of staff desk.
  useEffect(() => {
    if (fittedRef.current) return undefined;
    let tries = 0;
    const tick = () => {
      tries += 1;
      if (fitViewRef.current()) {
        fittedRef.current = true;
        return;
      }
      if (tries < 20) window.setTimeout(tick, 50);
    };
    const t = window.setTimeout(tick, 30);
    return () => window.clearTimeout(t);
  }, []);

  // Mobile URL-bar resize must not reset the camera — only re-clamp pan.
  useEffect(() => {
    let timer = null;
    const onResize = () => {
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        const el = viewportRef.current;
        if (!el) return;
        if (el.offsetParent === null && el.getClientRects().length === 0) return;
        const { width, height } = el.getBoundingClientRect();
        if (width < 120 || height < 120) return;
        const next = clampPan(
          panRef.current,
          zoomRef.current,
          width,
          height
        );
        panRef.current = next;
        setPan(next);
      }, 120);
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      if (timer) window.clearTimeout(timer);
    };
  }, []);

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
      markUserCamera();
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

  const applyZoomAt = (mx, my, nextZoom, width, height) => {
    markUserCamera();
    const z = zoomRef.current;
    const p = panRef.current;
    const wx = (mx - p.x) / z;
    const wy = (my - p.y) / z;
    const nextPan = clampPan(
      { x: mx - wx * nextZoom, y: my - wy * nextZoom },
      nextZoom,
      width || 1,
      height || 1
    );
    zoomRef.current = nextZoom;
    panRef.current = nextPan;
    setZoom(nextZoom);
    setPan(nextPan);
  };

  const pinchMetrics = (pts) => {
    const [a, b] = pts;
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    return {
      dist: Math.hypot(dx, dy) || 1,
      mid: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 },
    };
  };

  const onPointerDown = (e) => {
    if (e.pointerType === "mouse" && e.button !== 0 && e.button !== 1) return;
    if (e.target.closest(".bn-flow-controls")) return;
    const el = e.currentTarget;
    const rect = el.getBoundingClientRect();
    const pts = pointersRef.current;
    pts.set(e.pointerId, {
      x: e.clientX - rect.left,
      y: e.clientY - rect.top,
      cx: e.clientX,
      cy: e.clientY,
    });

    if (pts.size === 1) {
      // Do not capture — a second finger must still reach this element for pinch.
      const nodeEl = e.target.closest?.("[data-node-id]");
      dragRef.current = {
        x: e.clientX,
        y: e.clientY,
        panX: panRef.current.x,
        panY: panRef.current.y,
        moved: false,
        nodeId: nodeEl?.getAttribute("data-node-id") || null,
      };
      pinchRef.current = null;
      el.classList.add("is-panning");
    } else if (pts.size >= 2) {
      dragRef.current = null;
      const pair = [...pts.values()].slice(0, 2);
      const m = pinchMetrics(pair);
      pinchRef.current = {
        startDist: m.dist,
        startZoom: zoomRef.current,
        lastMid: m.mid,
      };
      el.classList.add("is-panning");
    }
  };

  const onPointerMove = (e) => {
    const el = viewportRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const pts = pointersRef.current;
    if (!pts.has(e.pointerId)) return;
    pts.set(e.pointerId, {
      x: e.clientX - rect.left,
      y: e.clientY - rect.top,
      cx: e.clientX,
      cy: e.clientY,
    });

    if (pts.size >= 2 && pinchRef.current) {
      const pair = [...pts.values()].slice(0, 2);
      const m = pinchMetrics(pair);
      const pinch = pinchRef.current;
      const ratio = m.dist / pinch.startDist;
      const next = Math.min(
        MAX_ZOOM,
        Math.max(MIN_ZOOM, pinch.startZoom * ratio)
      );
      // Zoom about current midpoint, then pan by midpoint drift (Maps-style).
      applyZoomAt(m.mid.x, m.mid.y, next, rect.width, rect.height);
      const dx = m.mid.x - pinch.lastMid.x;
      const dy = m.mid.y - pinch.lastMid.y;
      if (dx || dy) {
        const shifted = clampPan(
          { x: panRef.current.x + dx, y: panRef.current.y + dy },
          zoomRef.current,
          rect.width,
          rect.height
        );
        panRef.current = shifted;
        setPan(shifted);
      }
      pinch.lastMid = m.mid;
      return;
    }

    const d = dragRef.current;
    if (!d || pts.size !== 1) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    if (Math.abs(dx) + Math.abs(dy) > 4) d.moved = true;
    if (!d.moved) return;
    markUserCamera();
    const next = clampPan(
      { x: d.panX + dx, y: d.panY + dy },
      zoomRef.current,
      rect.width || 1,
      rect.height || 1
    );
    panRef.current = next;
    setPan(next);
  };

  const onPointerUp = (e) => {
    const el = viewportRef.current;
    const pts = pointersRef.current;
    pts.delete(e.pointerId);

    if (pts.size >= 2) {
      const pair = [...pts.values()].slice(0, 2);
      const m = pinchMetrics(pair);
      pinchRef.current = {
        startDist: m.dist,
        startZoom: zoomRef.current,
        lastMid: m.mid,
      };
      dragRef.current = null;
      return;
    }

    if (pts.size === 1) {
      pinchRef.current = null;
      const remaining = [...pts.values()][0];
      dragRef.current = {
        x: remaining.cx,
        y: remaining.cy,
        panX: panRef.current.x,
        panY: panRef.current.y,
        moved: true,
        nodeId: null,
      };
      return;
    }

    const d = dragRef.current;
    dragRef.current = null;
    pinchRef.current = null;
    el?.classList.remove("is-panning");
    if (d && !d.moved && d.nodeId) setActiveId(d.nodeId);
  };

  const active = nodes.find((n) => n.id === activeId) || null;

  const metaLine = trace
    ? `${route || "—"} · ${
        trace.latency_ms != null ? `${trace.latency_ms} ms` : "—"
      }${trace.language ? ` · ${trace.language}` : ""}${
        liveOn ? " · live" : ""
      }`
    : "Pinch · drag · click a node";

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
              markUserCamera();
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
              markUserCamera();
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
            onClick={() => {
              userCameraRef.current = false;
              if (trace) fitLitPath();
              else fitView();
            }}
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
            {withCrossingJumps(
              EDGES.map((edge) => {
                const geo = edgeGeometry(edge.from, edge.to);
                if (!geo) return null;
                return { ...geo, edge };
              })
            ).map((geo) => {
              if (!geo?.edge) return null;
              const edge = geo.edge;
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
                  />
                  {geo.arrow ? (
                    <path
                      d={geo.arrow}
                      className={`bn-pipe-arrow${lit ? " is-lit" : ""}`}
                    />
                  ) : null}
                  {label && geo.labelAt ? (
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
                    hoursApisOn={hoursApisOn}
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
