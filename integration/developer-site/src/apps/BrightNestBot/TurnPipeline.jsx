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
  staff_email: "Staff email draft (cheap)",
  main_generate: "Main generate",
  cheap_llm: "Cheap LLM",
  cheap_json: "Cheap JSON",
};

/**
 * Compact top-down layout (world px, origin at spine).
 * Spine: parent → memory → router → tier → … → answer
 * Side fans: rules/arbiter, soft|hard|rag, tools/stores
 */
const LAYOUT = {
  parent: { x: 0, y: 0, w: 156, h: 42 },
  memory: { x: 0, y: 78, w: 132, h: 58 },
  router: { x: 0, y: 168, w: 146, h: 46 },
  rules: { x: -168, y: 248, w: 132, h: 46 },
  arbiter: { x: 168, y: 248, w: 132, h: 46 },
  tier: { x: 0, y: 348, w: 108, h: 108 },
  soft: { x: -188, y: 468, w: 136, h: 46 },
  hard: { x: 0, y: 468, w: 136, h: 46 },
  rag: { x: 188, y: 468, w: 136, h: 46 },
  hko: { x: -258, y: 558, w: 124, h: 46 },
  chroma: { x: 188, y: 558, w: 124, h: 58 },
  llm: { x: -70, y: 658, w: 146, h: 46 },
  sheets: { x: 130, y: 658, w: 132, h: 58 },
  answer: { x: 0, y: 748, w: 156, h: 42 },
};

/** Spine: parent → memory → router → rules → tier → branch…  Arbiter is optional side. */
const EDGES = [
  { id: "e-parent-mem", from: "parent", to: "memory" },
  { id: "e-mem-router", from: "memory", to: "router" },
  { id: "e-router-rules", from: "router", to: "rules" },
  { id: "e-rules-tier", from: "rules", to: "tier" },
  { id: "e-router-arb", from: "router", to: "arbiter", dashed: true },
  { id: "e-arb-tier", from: "arbiter", to: "tier", dashed: true },
  { id: "e-tier-soft", from: "tier", to: "soft", label: "live" },
  { id: "e-tier-hard", from: "tier", to: "hard", label: "hard" },
  { id: "e-tier-rag", from: "tier", to: "rag", label: "RAG" },
  { id: "e-soft-hko", from: "soft", to: "hko" },
  { id: "e-soft-llm", from: "soft", to: "llm" },
  { id: "e-soft-chroma", from: "soft", to: "chroma", dashed: true },
  { id: "e-hard-llm", from: "hard", to: "llm" },
  { id: "e-hard-sheets", from: "hard", to: "sheets" },
  { id: "e-rag-chroma", from: "rag", to: "chroma" },
  { id: "e-chroma-llm", from: "chroma", to: "llm" },
  { id: "e-hko-llm", from: "hko", to: "llm", dashed: true },
  { id: "e-llm-answer", from: "llm", to: "answer" },
  { id: "e-sheets-answer", from: "sheets", to: "answer" },
];

const WORLD = { minX: -340, minY: -28, maxX: 340, maxY: 820 };
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

function tierForRoute(route, grounding) {
  const g = grounding?.mode;
  if (g === "pure_rag" || route === "rag") return "rag";
  if (
    route === "booking_dialogue" ||
    route === "cancel_dialogue" ||
    route === "staff_escalation" ||
    route === "off_domain" ||
    g === "bypass"
  ) {
    return "hard";
  }
  if (route) return "soft";
  return null;
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
  if (id === "tier") {
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

  if (fromId === "tier" && toId === "soft") {
    rawStart = nodeAnchor("tier", "sw");
    rawEnd = nodeAnchor(toId, "top");
  } else if (fromId === "tier" && toId === "rag") {
    rawStart = nodeAnchor("tier", "se");
    rawEnd = nodeAnchor(toId, "top");
  } else if (fromId === "tier" && toId === "hard") {
    rawStart = nodeAnchor("tier", "bottom");
    rawEnd = nodeAnchor(toId, "top");
  } else if (fromId === "router" && toId === "rules") {
    rawStart = nodeAnchor("router", "left");
    rawEnd = nodeAnchor("rules", "top");
  } else if (fromId === "router" && toId === "arbiter") {
    rawStart = nodeAnchor("router", "right");
    rawEnd = nodeAnchor("arbiter", "top");
  } else if (fromId === "rules" && toId === "tier") {
    rawStart = nodeAnchor("rules", "bottom");
    rawEnd = nodeAnchor("tier", "left");
  } else if (fromId === "arbiter" && toId === "tier") {
    rawStart = nodeAnchor("arbiter", "bottom");
    rawEnd = nodeAnchor("tier", "right");
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
  } else if (fromId === "router" || toId === "tier") {
    // Orthogonal elbow for fan-ins/outs
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
        {hit.char_count != null ? (
          <div className="bn-hit-meta">
            {hit.char_count} chars · score {score ?? "—"}
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
                  <strong>{c.passed ? "pass" : "fail"}</strong>
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
  tier,
  finalCheck,
  adminTodo,
  staffMessage,
  scoreLedger,
}) {
  if (!node) return null;
  if (!trace) {
    return (
      <p className="bn-empty">
        Send a parent message to populate this stage. Click again after a turn.
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
          <p className="bn-rules-hint">
            Inbound parent text for this turn — before routing or tools.
          </p>
          <ul className="bn-fact-list">
            <li>
              <span>Language</span>
              <code>{trace.language || "—"}</code>
            </li>
            <li>
              <span>Session</span>
              <code>{trace.session_id || "—"}</code>
            </li>
          </ul>
          <pre className="bn-pre bn-pre-tall">{inbound || "(empty)"}</pre>
        </>
      );

    case "memory": {
      const loaded = priorTurns.length;
      return (
        <>
          <p className="bn-rules-hint">
            Session memory <strong>before</strong> this inbound message was
            appended — what the router and models could already see.
          </p>
          <ul className="bn-fact-list">
            <li>
              <span>Prior turns loaded</span>
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
          {priorPreview ? (
            <details className="bn-llm-part" open>
              <summary>
                Prior context only
                <em>{String(priorPreview).length} chars</em>
              </summary>
              <pre className="bn-pre bn-pre-tall">{priorPreview}</pre>
            </details>
          ) : (
            <p className="bn-note">
              No prior turns — first message in this session.
            </p>
          )}
          {priorTurns.length > 0 ? (
            <ul className="bn-mem-turns">
              {priorTurns.map((t, i) => (
                <li key={`mem-${i}`}>
                  <details>
                    <summary>
                      <strong>
                        {t.role === "user" || t.role === "parent"
                          ? "parent"
                          : "bot"}
                      </strong>
                      <span>{clip(t.content || "", 100)}</span>
                    </summary>
                    <pre className="bn-hit-full">{t.content || ""}</pre>
                  </details>
                </li>
              ))}
            </ul>
          ) : null}
        </>
      );
    }

    case "router":
      return (
        <>
          <p className="bn-rules-hint">
            Hybrid intent pick for this inbound — scores and rules live on their
            own nodes.
          </p>
          <ul className="bn-fact-list">
            <li>
              <span>Picked route</span>
              <code>{route || "—"}</code>
            </li>
            <li>
              <span>Winning rule</span>
              <code>{winningRuleId || "—"}</code>
            </li>
            {routing?.hours_followup_promoted ? (
              <li>
                <span>Hours follow-up</span>
                <code>promoted</code>
              </li>
            ) : null}
            {routing?.dialogue_active ? (
              <li>
                <span>Booking sticky</span>
                <code>active</code>
              </li>
            ) : null}
            {routing?.cancel_active ? (
              <li>
                <span>Cancel sticky</span>
                <code>active</code>
              </li>
            ) : null}
          </ul>
          <MatchSummaryCard summary={matchSummary} />
        </>
      );

    case "rules":
      return (
        <>
          <div className="bn-score-howto">
            <p>
              <strong>How a route wins</strong>
            </p>
            <ol>
              <li>Each detector that matches awards a <em>fixed</em> score (not RAG similarity).</li>
              <li>Drop anything under <code>0.28</code>.</li>
              <li>Highest score wins. Ties break by priority: escalate → off-domain → weather → cancel → book → seats → hours → RAG.</li>
              <li>If nothing matched → RAG at <code>0.5</code>.</li>
            </ol>
          </div>
          {routing?.scores && Object.keys(routing.scores).length > 0 ? (
            <details className="bn-rules" open>
              <summary>Scores · detector awards</summary>
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
                        <code>{Number(v).toFixed(3)}</code>
                      </li>
                    );
                  })}
              </ul>
              {scoreLedger.length > 0 ? (
                <details className="bn-rules bn-ledger">
                  <summary>Ledger · {scoreLedger.length} steps</summary>
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
                            ? ` = ${Number(e.score).toFixed(3)}`
                            : ""}
                          {" · "}
                          {e.source}
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
              Route arbiter did not run this turn
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
              <span>Action</span>
              <code>{arbiter?.action || "—"}</code>
            </li>
            <li>
              <span>Model</span>
              <code>{arbiter?.model || "—"}</code>
            </li>
          </ul>
          {arbiter?.reason ? <p className="bn-note">{arbiter.reason}</p> : null}
          {arbiter?.raw ? (
            <pre className="bn-pre">{String(arbiter.raw)}</pre>
          ) : null}
          {arbCalls.map((c, i) => (
            <LlmCallCard key={`arb-${i}`} call={c} defaultOpen />
          ))}
        </>
      );
    }

    case "tier":
      return (
        <>
          <p className="bn-rules-hint">
            Decision for this turn — which outbound branch lit up.
          </p>
          <ul className="bn-fact-list">
            <li>
              <span>Branch</span>
              <code>
                {tier === "rag"
                  ? "pure_rag"
                  : tier === "hard"
                    ? "hard_fsm"
                    : tier === "soft"
                      ? "live_structured"
                      : "—"}
              </code>
            </li>
            <li>
              <span>Route</span>
              <code>{route || "—"}</code>
            </li>
            <li>
              <span>Grounding</span>
              <code>{grounding?.mode || "—"}</code>
            </li>
          </ul>
          {grounding?.why ? <p className="bn-note">{grounding.why}</p> : null}
        </>
      );

    case "soft":
      if (tier !== "soft") {
        return (
          <Skipped>
            Live / structured branch not taken — tier was{" "}
            <code>{tier || "unset"}</code>.
          </Skipped>
        );
      }
      return (
        <>
          <ul className="bn-fact-list">
            <li>
              <span>Live doc</span>
              <code>
                {grounding?.live_doc
                  ? `${grounding.live_doc_chars || "?"} chars`
                  : "no"}
              </code>
            </li>
            <li>
              <span>Composed</span>
              <code>{grounding?.composed ? "yes" : "no"}</code>
            </li>
            <li>
              <span>Docs privileged</span>
              <code>{grounding?.structured_privileged ? "yes" : "no"}</code>
            </li>
          </ul>
          {grounding?.why ? <p className="bn-note">{grounding.why}</p> : null}
          {trace?.adapt ? (
            <details className="bn-llm-part">
              <summary>Adapt meta</summary>
              <pre className="bn-pre">{JSON.stringify(trace.adapt, null, 2)}</pre>
            </details>
          ) : null}
          {llmCalls
            .filter((c) =>
              ["grounded_compose", "fact_adapt"].includes(c.role)
            )
            .map((c, i) => (
              <LlmCallCard
                key={`soft-llm-${i}`}
                call={c}
                defaultOpen={i === 0}
              />
            ))}
        </>
      );

    case "hard":
      if (tier !== "hard") {
        return (
          <Skipped>
            Hard FSM branch not taken — tier was <code>{tier || "unset"}</code>.
          </Skipped>
        );
      }
      return (
        <>
          <ul className="bn-fact-list">
            <li>
              <span>Booking stage</span>
              <code>{trace?.booking?.stage || "—"}</code>
            </li>
            <li>
              <span>Cancel stage</span>
              <code>{trace?.cancel?.stage || "—"}</code>
            </li>
            <li>
              <span>Final check</span>
              <code>{finalCheck?.action || "—"}</code>
            </li>
          </ul>
          {finalCheck ? (
            <details className="bn-llm-part" open>
              <summary>Final check</summary>
              <pre className="bn-pre bn-pre-tall">
                {JSON.stringify(finalCheck, null, 2)}
              </pre>
            </details>
          ) : null}
          {trace?.booking ? (
            <details className="bn-llm-part">
              <summary>Booking trace</summary>
              <pre className="bn-pre bn-pre-tall">
                {JSON.stringify(trace.booking, null, 2)}
              </pre>
            </details>
          ) : null}
          {trace?.cancel ? (
            <details className="bn-llm-part">
              <summary>Cancel trace</summary>
              <pre className="bn-pre bn-pre-tall">
                {JSON.stringify(trace.cancel, null, 2)}
              </pre>
            </details>
          ) : null}
          {llmCalls
            .filter(
              (c) =>
                String(c.role || "").startsWith("final_check") ||
                ["booking_parse", "cancel_pick", "staff_email"].includes(
                  c.role
                )
            )
            .map((c, i) => (
              <LlmCallCard
                key={`hard-llm-${i}`}
                call={c}
                defaultOpen={i === 0}
              />
            ))}
        </>
      );

    case "rag":
      if (tier !== "rag") {
        return (
          <Skipped>
            Pure RAG branch not taken — tier was <code>{tier || "unset"}</code>.
            {enrichMode
              ? " Soft path may still enrich via Chroma."
              : ""}
          </Skipped>
        );
      }
      return (
        <>
          <ul className="bn-fact-list">
            <li>
              <span>Hits</span>
              <code>{rag?.hit_count ?? hits.length}</code>
            </li>
            <li>
              <span>Top score</span>
              <code>
                {scoreLabel(rag?.top_score ?? hits[0]?.score) ?? "—"}
              </code>
            </li>
            <li>
              <span>Gap</span>
              <code>{rag?.gap_reason || "—"}</code>
            </li>
          </ul>
          <p className="bn-note">
            Chunks are on <strong>Chroma KB</strong>; model I/O on{" "}
            <strong>DashScope</strong>.
          </p>
        </>
      );

    case "hko":
      if (!facts) {
        return (
          <Skipped>
            No hours / holiday / HKO check on this turn.
          </Skipped>
        );
      }
      return (
        <>
          <p className="bn-rules-hint">
            Holiday check on the resolved date; live HKO only when that date is
            today.
          </p>
          {(facts.checks || []).length > 0 ? (
            <ul className="bn-rule-checks bn-hours-checks">
              {facts.checks.map((c) => (
                <li
                  key={c.id || c.label}
                  className={c.passed ? "pass" : "fail"}
                >
                  <strong>
                    {c.passed ? "hit" : c.checked ? "clear" : "skip"}
                  </strong>
                  <span>
                    {c.label}
                    {c.detail ? ` — ${c.detail}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
          <ul className="bn-fact-list">
            <li>
              <span>Resolved</span>
              <code>{facts.datetime}</code>
            </li>
            <li>
              <span>Matched via</span>
              <code>
                {facts.parse_debug?.matched_via ||
                  (facts.parse_debug?.is_fallback_to_now
                    ? "fallback_now"
                    : "—")}
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
              <code>
                {facts.is_holiday
                  ? `YES · ${facts.holiday_name || "public holiday"}`
                  : "NO · not a public holiday"}
              </code>
            </li>
            <li>
              <span>HKO</span>
              <code>
                {facts.weather_hint
                  ? `CLOSURE · ${facts.weather_hint}`
                  : facts.parse_debug?.weather_applied
                    ? "CHECKED · no severe signal today"
                    : facts.parse_debug?.weather_same_day_only
                      ? "SKIPPED · date is not today"
                      : "not run"}
              </code>
            </li>
            {trace?.opening_hours?.effective_message ? (
              <li>
                <span>Effective msg</span>
                <code>{trace.opening_hours.effective_message}</code>
              </li>
            ) : null}
          </ul>
        </>
      );

    case "chroma":
      if (!(hits.length || enrichMode || tier === "rag")) {
        return (
          <Skipped>
            KB retrieve did not run this turn (hard / tool path with no enrich).
          </Skipped>
        );
      }
      if (!hits.length) {
        return <Skipped>Retrieve ran but no chunks returned.</Skipped>;
      }
      return (
        <>
          {enrichMode ? (
            <p className="bn-note">
              Enrichment chunks — structured <code>{route}</code> card stays
              source of truth.
            </p>
          ) : null}
          <div className="bn-rag-strip">
            <span>index {rag?.index_size ?? "—"}</span>
            <span>top_k {rag?.top_k ?? hits.length}</span>
            <span>hits {rag?.hit_count ?? hits.length}</span>
            <span>
              top {scoreLabel(rag?.top_score ?? hits[0]?.score) ?? "—"}
            </span>
            <span>{rag?.embedding_mode || "—"}</span>
            {enrichMode ? <span>grounded_enrich</span> : null}
            {rag?.language_fallback ? <span>lang fallback</span> : null}
            {rag?.gap_reason ? <span>gap {rag.gap_reason}</span> : null}
          </div>
          <ul className="bn-hits">
            {hits.map((h, i) => (
              <RagHit key={`${h.source}-${h.rank || i}`} hit={h} index={i} />
            ))}
          </ul>
        </>
      );

    case "llm": {
      const genCalls = llmCalls.filter((c) => c.role !== "intent_arbiter");
      return (
        <>
          <p className="bn-rules-hint">
            DashScope calls for this turn (arbiter I/O is on the Arbiter node).
            Context below includes the inbound message.
          </p>
          {history?.preview ? (
            <details className="bn-llm-part bn-llm-context">
              <summary>
                Prompt context string (prior + inbound)
                <em>{String(history.preview).length} chars</em>
              </summary>
              <pre className="bn-pre bn-pre-tall">{history.preview}</pre>
            </details>
          ) : null}
          {genCalls.length === 0 ? (
            <Skipped>
              No generation / final-check DashScope call (deterministic
              template).
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
          <Skipped>No SheetPro / MailBox / CSV write this turn.</Skipped>
        );
      }
      return (
        <>
          {adminTodo ? (
            <details className="bn-llm-part" open>
              <summary>SheetPro admin todo</summary>
              <pre className="bn-pre bn-pre-tall">
                {JSON.stringify(adminTodo, null, 2)}
              </pre>
            </details>
          ) : null}
          {staffMessage ? (
            <details className="bn-llm-part" open>
              <summary>MailBox staff message</summary>
              <pre className="bn-pre bn-pre-tall">
                {JSON.stringify(staffMessage, null, 2)}
              </pre>
            </details>
          ) : null}
          {trace?.booking?.booking || trace?.booking?.stage === "booked" ? (
            <details className="bn-llm-part" open>
              <summary>Chat booking</summary>
              <pre className="bn-pre bn-pre-tall">
                {JSON.stringify(
                  trace.booking.booking || trace.booking,
                  null,
                  2
                )}
              </pre>
            </details>
          ) : null}
          {trace?.cancel ? (
            <details className="bn-llm-part" open>
              <summary>Cancel / CSV</summary>
              <pre className="bn-pre bn-pre-tall">
                {JSON.stringify(trace.cancel, null, 2)}
              </pre>
            </details>
          ) : null}
        </>
      );
    }

    case "answer":
      return (
        <>
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
  const tier = tierForRoute(route, grounding);
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
    return [
      {
        id: "parent",
        label: "Parent message",
        kind: "start",
        shape: "terminal",
        status: hasTrace ? "done" : "idle",
      },
      {
        id: "memory",
        label: "Session memory",
        kind: "store",
        shape: "cylinder",
        // Always consulted when history is enabled — empty prior is still a real load.
        status: !hasTrace
          ? "idle"
          : history?.enabled === false
            ? "skipped"
            : memLoaded
              ? "done"
              : "empty",
      },
      {
        id: "router",
        label: "Hybrid router",
        kind: "router",
        shape: "process",
        status: hasTrace ? "done" : "idle",
      },
      {
        id: "rules",
        label: "Rules · scores",
        kind: "rules",
        shape: "process",
        status: !hasTrace
          ? "idle"
          : rulesEvaluated.length
            ? "done"
            : "skipped",
      },
      {
        id: "arbiter",
        label: arbiter?.called ? "Route arbiter" : "Arbiter",
        kind: "llm",
        shape: "process",
        status: !hasTrace ? "idle" : arbiter?.called ? "done" : "skipped",
      },
      {
        id: "tier",
        label: "Answer tier?",
        kind: "branch",
        shape: "decision",
        status: hasTrace && route ? "done" : "idle",
      },
      {
        id: "soft",
        label: "Live / structured",
        kind: "compose",
        shape: "process",
        status: !hasTrace ? "idle" : tier === "soft" ? "done" : "skipped",
      },
      {
        id: "hard",
        label: "Hard FSM",
        kind: "hard",
        shape: "process",
        status: !hasTrace ? "idle" : tier === "hard" ? "done" : "skipped",
      },
      {
        id: "rag",
        label: "Pure RAG",
        kind: "rag",
        shape: "process",
        status: !hasTrace ? "idle" : tier === "rag" ? "done" : "skipped",
      },
      {
        id: "hko",
        label: "HKO · holiday",
        kind: "live",
        shape: "process",
        status: !hasTrace ? "idle" : facts ? "done" : "skipped",
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
            : tier === "rag" || enrichMode
              ? "empty"
              : "skipped",
      },
      {
        id: "llm",
        label: llmCalls.filter((c) => c.role !== "intent_arbiter").length
          ? `DashScope · ${
              llmCalls.filter((c) => c.role !== "intent_arbiter").length
            }`
          : "DashScope",
        kind: "llm",
        shape: "process",
        status: !hasTrace
          ? "idle"
          : llmCalls.some((c) => c.role !== "intent_arbiter")
            ? "done"
            : "skipped",
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
    arbiter,
    tier,
    facts,
    hits.length,
    rag?.hit_count,
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
    // Memory is always on the spine when history ran (even 0 prior turns).
    const path = new Set(["parent", "memory", "router", "rules", "tier", "answer"]);
    if (history?.enabled === false) path.delete("memory");
    if (arbiter?.called) path.add("arbiter");
    if (tier === "soft") {
      path.add("soft");
      if (facts) path.add("hko");
      if (hits.length || enrichMode) path.add("chroma");
    } else if (tier === "hard") {
      path.add("hard");
      if (
        adminTodo ||
        staffMessage ||
        trace?.booking?.stage === "booked" ||
        trace?.cancel?.stage === "cancelled"
      ) {
        path.add("sheets");
      }
    } else if (tier === "rag") {
      path.add("rag");
      path.add("chroma");
      if (adminTodo) path.add("sheets");
    }
    if (llmCalls.some((c) => c.role !== "intent_arbiter")) path.add("llm");
    return path;
  }, [
    trace,
    history?.enabled,
    arbiter,
    tier,
    facts,
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
      (id === "memory" && on(id));
    const link = (from, to, edgeId) => {
      if (on(from) && on(to) && ok(from) && ok(to)) lit.add(edgeId);
    };
    link("parent", "memory", "e-parent-mem");
    link("memory", "router", "e-mem-router");
    link("router", "rules", "e-router-rules");
    link("rules", "tier", "e-rules-tier");
    if (arbiter?.called) {
      link("router", "arbiter", "e-router-arb");
      link("arbiter", "tier", "e-arb-tier");
    }
    if (tier === "soft") link("tier", "soft", "e-tier-soft");
    if (tier === "hard") link("tier", "hard", "e-tier-hard");
    if (tier === "rag") link("tier", "rag", "e-tier-rag");
    if (on("soft") && on("hko")) link("soft", "hko", "e-soft-hko");
    if (on("soft") && on("llm")) link("soft", "llm", "e-soft-llm");
    if (on("soft") && on("chroma")) link("soft", "chroma", "e-soft-chroma");
    if (on("hard") && on("llm")) link("hard", "llm", "e-hard-llm");
    if (on("hard") && on("sheets")) link("hard", "sheets", "e-hard-sheets");
    if (on("rag") && on("chroma")) link("rag", "chroma", "e-rag-chroma");
    if (on("chroma") && on("llm")) link("chroma", "llm", "e-chroma-llm");
    if (on("hko") && on("llm")) link("hko", "llm", "e-hko-llm");
    if (on("llm")) link("llm", "answer", "e-llm-answer");
    if (on("sheets")) link("sheets", "answer", "e-sheets-answer");
    return lit;
  }, [trace, activePath, statusById, arbiter, tier]);

  const edgeLabels = useMemo(() => {
    const priorN = Number(history?.turns_loaded || 0);
    const win =
      matchSummary?.cue
        ? `«${clip(matchSummary.cue, 18)}»`
        : winningRuleId || route || "";
    const labels = {
      "e-parent-mem": "inbound",
      "e-mem-router":
        priorN > 0
          ? `${priorN} prior msg${priorN === 1 ? "" : "s"}`
          : "0 prior",
      "e-router-rules": "detectors",
      "e-rules-tier": win ? `→ ${win}` : "pick",
      "e-router-arb": arbiter?.called ? "review" : "",
      "e-arb-tier": arbiter?.action || "",
      "e-tier-soft": "live",
      "e-tier-hard": "hard",
      "e-tier-rag": "RAG",
      "e-soft-hko": facts ? "hours" : "",
      "e-soft-chroma": enrichMode ? "enrich" : "",
      "e-rag-chroma": hits.length ? `${hits.length} hits` : "retrieve",
      "e-chroma-llm": hits.length ? `top ${scoreLabel(hits[0]?.score) || "—"}` : "",
      "e-hard-sheets": adminTodo || staffMessage ? "write" : "",
      "e-llm-answer": "reply",
    };
    return labels;
  }, [
    history?.turns_loaded,
    matchSummary,
    winningRuleId,
    route,
    arbiter,
    facts,
    enrichMode,
    hits,
    adminTodo,
    staffMessage,
  ]);

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
      }${
        tier
          ? ` · ${
              tier === "rag"
                ? "pure RAG"
                : tier === "hard"
                  ? "hard FSM"
                  : "live / structured"
            }`
          : ""
      }`
    : "Idle — drag to pan · scroll to zoom · click a node";

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
          >
            −
          </button>
          <button type="button" onClick={fitView} title="Fit">
            Fit
          </button>
          <span className="bn-flow-zoom">{Math.round(zoom * 100)}%</span>
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
                    <span className="bn-pipe-shape-tag">{active.shape}</span>
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
                    tier={tier}
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
