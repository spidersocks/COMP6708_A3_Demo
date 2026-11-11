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

/** World-space layout (px). Origin ≈ top-centre of the graph. */
const LAYOUT = {
  parent: { x: 0, y: 0, w: 168, h: 48 },
  memory: { x: 0, y: 110, w: 150, h: 64 },
  router: { x: 0, y: 230, w: 158, h: 52 },
  rules: { x: -210, y: 340, w: 148, h: 52 },
  arbiter: { x: 210, y: 340, w: 148, h: 52 },
  tier: { x: 0, y: 470, w: 136, h: 136 },
  soft: { x: -250, y: 660, w: 150, h: 52 },
  hard: { x: 0, y: 660, w: 150, h: 52 },
  rag: { x: 250, y: 660, w: 150, h: 52 },
  hko: { x: -340, y: 790, w: 140, h: 52 },
  chroma: { x: 250, y: 790, w: 140, h: 68 },
  llm: { x: -80, y: 920, w: 158, h: 52 },
  sheets: { x: 160, y: 920, w: 150, h: 68 },
  answer: { x: 0, y: 1050, w: 168, h: 48 },
};

const EDGES = [
  { from: "parent", to: "memory" },
  { from: "memory", to: "router" },
  { from: "router", to: "rules" },
  { from: "router", to: "arbiter" },
  { from: "rules", to: "tier" },
  { from: "arbiter", to: "tier" },
  { from: "router", to: "tier" },
  { from: "tier", to: "soft", label: "live" },
  { from: "tier", to: "hard", label: "hard" },
  { from: "tier", to: "rag", label: "RAG" },
  { from: "soft", to: "hko" },
  { from: "soft", to: "llm" },
  { from: "soft", to: "chroma", dashed: true },
  { from: "hard", to: "llm" },
  { from: "hard", to: "sheets" },
  { from: "rag", to: "chroma" },
  { from: "chroma", to: "llm" },
  { from: "llm", to: "answer" },
  { from: "sheets", to: "answer" },
  { from: "hko", to: "llm", dashed: true },
];

const WORLD = { minX: -460, minY: -40, maxX: 460, maxY: 1140 };
const MIN_ZOOM = 0.45;
const MAX_ZOOM = 1.85;

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

function nodeAnchor(id, side = "bottom") {
  const n = LAYOUT[id];
  if (!n) return { x: 0, y: 0 };
  const cx = n.x;
  const cy = n.y;
  if (side === "top") return { x: cx, y: cy - n.h / 2 };
  if (side === "left") return { x: cx - n.w / 2, y: cy };
  if (side === "right") return { x: cx + n.w / 2, y: cy };
  if (side === "center") return { x: cx, y: cy };
  return { x: cx, y: cy + n.h / 2 };
}

function edgeGeometry(fromId, toId) {
  const a = LAYOUT[fromId];
  const b = LAYOUT[toId];
  if (!a || !b) return null;
  const start = nodeAnchor(fromId, "bottom");
  const end = nodeAnchor(toId, "top");
  // Decision diamond: leave from lower facets toward each branch
  if (fromId === "tier") {
    if (toId === "soft") {
      start.x = a.x - a.w * 0.28;
      start.y = a.y + a.h * 0.22;
    } else if (toId === "rag") {
      start.x = a.x + a.w * 0.28;
      start.y = a.y + a.h * 0.22;
    } else {
      start.x = a.x;
      start.y = a.y + a.h / 2;
    }
  }
  const midY = (start.y + end.y) / 2;
  const d = `M ${start.x} ${start.y} C ${start.x} ${midY}, ${end.x} ${midY}, ${end.x} ${end.y}`;
  const labelAt = {
    x: (start.x + end.x) / 2,
    y: midY - 6,
  };
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
                Intent scores are <strong>not</strong> RAG similarity. Hard
                detectors award fixed values (e.g. fees=0.90); prototypes may
                blend; conflicts clear losers.
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

function NodeDetail({
  node,
  trace,
  answer,
  chatHistory = [],
  route,
  grounding,
  routing,
  steps,
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
  if (!trace && node.id !== "memory") {
    return (
      <p className="bn-empty">
        Send a parent message to populate this node. After a turn, click again
        for the exact inputs, scores, and model I/O.
      </p>
    );
  }

  switch (node.id) {
    case "parent":
      return (
        <>
          <p className="bn-rules-hint">Inbound turn on this session.</p>
          <ul className="bn-fact-list">
            <li>
              <span>Route</span>
              <code>{route || "—"}</code>
            </li>
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
          <MatchSummaryCard summary={matchSummary} />
          {(history?.turns || []).length ? (
            <div className="bn-pipe-block">
              <h4>Latest parent turn in memory</h4>
              <pre className="bn-pre">
                {([...history.turns].reverse().find((t) =>
                  ["user", "parent"].includes(t.role)
                )?.content) || "(see chat)"}
              </pre>
            </div>
          ) : null}
        </>
      );
    case "memory": {
      const historyTurns = history?.turns || [];
      return (
        <>
          <ul className="bn-fact-list">
            <li>
              <span>Turns loaded</span>
              <code>
                {history?.turns_loaded ?? historyTurns.length ?? 0}/
                {history?.limit ?? "—"}
              </code>
            </li>
            <li>
              <span>Enabled</span>
              <code>{history?.enabled == null ? "—" : history.enabled ? "yes" : "no"}</code>
            </li>
          </ul>
          {history?.preview ? (
            <details className="bn-llm-part" open>
              <summary>
                Context string fed downstream
                <em>{String(history.preview).length} chars</em>
              </summary>
              <pre className="bn-pre bn-pre-tall">{history.preview}</pre>
            </details>
          ) : (
            <p className="bn-note">No prior turns in session memory for this call.</p>
          )}
          {historyTurns.length > 0 ? (
            <ul className="bn-mem-turns">
              {historyTurns.map((t, i) => (
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
        </>
      );
    }
    case "router":
      return (
        <>
          <ul className="bn-fact-list">
            <li>
              <span>Picked route</span>
              <code>{route || "—"}</code>
            </li>
            <li>
              <span>Winning rule</span>
              <code>{winningRuleId || "—"}</code>
            </li>
            <li>
              <span>Grounding</span>
              <code>{grounding?.mode || "—"}</code>
            </li>
          </ul>
          <MatchSummaryCard summary={matchSummary} />
          {grounding ? (
            <p className="bn-grounding-one-liner">
              <strong>Grounding</strong> · <code>{grounding.mode}</code>
              {grounding.structured_privileged ? " · docs privileged" : ""}
              {grounding.live_doc ? " · live doc" : ""}
              {grounding.composed ? " · KB enrich" : ""}
            </p>
          ) : null}
          {steps.length > 0 ? (
            <ol className="bn-step-rail" aria-label="Pipeline steps">
              {steps
                .filter((s) => s.status !== "skipped" || s.id === "final_check")
                .map((s) => (
                  <li
                    key={s.id}
                    className={`bn-step-chip bn-step-${s.status || "idle"}`}
                    title={s.detail}
                  >
                    <span>{s.label}</span>
                  </li>
                ))}
            </ol>
          ) : null}
        </>
      );
    case "rules":
      return (
        <>
          <p className="bn-rules-hint">
            Detector awards this turn — not RAG similarity. Expand a rule for
            cue, score origin, and checks.
          </p>
          <MatchSummaryCard summary={matchSummary} />
          {routing?.scores && Object.keys(routing.scores).length > 0 ? (
            <details className="bn-rules" open>
              <summary>Scores · detector awards (not RAG sim)</summary>
              <ul className="bn-fact-list">
                {Object.entries(routing.scores)
                  .sort((a, b) => Number(b[1]) - Number(a[1]))
                  .slice(0, 10)
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
                      <li key={`led-${i}`} className={`bn-led-${e.op || "note"}`}>
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
            <details className="bn-rules" open>
              <summary>
                Rules · {rulesEvaluated.filter((r) => r.matched).length} fired /{" "}
                {rulesEvaluated.length}
                {matchSummary?.cue ? ` · «${matchSummary.cue}»` : ""}
              </summary>
              <ul className="bn-rule-list">
                {rulesEvaluated.map((r) => (
                  <RuleEvidence
                    key={r.id}
                    rule={r}
                    isWinner={r.id === winningRuleId && Boolean(r.matched)}
                  />
                ))}
              </ul>
            </details>
          ) : (
            <p className="bn-empty">No rule trace on this turn.</p>
          )}
        </>
      );
    case "arbiter": {
      const arbCalls = llmCalls.filter((c) => c.role === "intent_arbiter");
      return (
        <>
          <ul className="bn-fact-list">
            <li>
              <span>Called</span>
              <code>{arbiter?.called ? "yes" : "no"}</code>
            </li>
            <li>
              <span>Action</span>
              <code>{arbiter?.action || arbiter?.reason || "skipped"}</code>
            </li>
            <li>
              <span>Model</span>
              <code>{arbiter?.model || "—"}</code>
            </li>
          </ul>
          {arbiter?.reason ? <p className="bn-note">{arbiter.reason}</p> : null}
          {arbiter?.skip_detail ? (
            <p className="bn-note">{arbiter.skip_detail}</p>
          ) : null}
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
            Decision diamond — which answer tier won this turn.
          </p>
          <ul className="bn-fact-list">
            <li>
              <span>Tier</span>
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
              <span>Grounding mode</span>
              <code>{grounding?.mode || "—"}</code>
            </li>
          </ul>
          {grounding ? (
            <p className="bn-grounding-one-liner">
              <strong>Grounding</strong> · <code>{grounding.mode}</code>
              {grounding.structured_privileged ? " · docs privileged" : ""}
              {grounding.live_doc ? " · live doc" : ""}
              {grounding.composed ? " · KB enrich" : ""}
            </p>
          ) : null}
          {grounding?.why ? <p className="bn-note">{grounding.why}</p> : null}
          {trace?.adapt ? (
            <pre className="bn-pre">{JSON.stringify(trace.adapt, null, 2)}</pre>
          ) : null}
        </>
      );
    case "soft":
      return (
        <>
          <p className="bn-rules-hint">
            Live / structured path — privileged centre docs may be composed with
            KB enrichment.
          </p>
          <ul className="bn-fact-list">
            <li>
              <span>Active</span>
              <code>{tier === "soft" ? "yes" : "no"}</code>
            </li>
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
              <span>Structured privileged</span>
              <code>{grounding?.structured_privileged ? "yes" : "no"}</code>
            </li>
          </ul>
          {grounding?.why ? <p className="bn-note">{grounding.why}</p> : null}
          {llmCalls
            .filter((c) =>
              ["grounded_compose", "fact_adapt"].includes(c.role)
            )
            .map((c, i) => (
              <LlmCallCard key={`soft-llm-${i}`} call={c} defaultOpen={i === 0} />
            ))}
        </>
      );
    case "hard":
      return (
        <>
          <p className="bn-rules-hint">
            Booking / cancel / escalate / refuse — retrieve usually bypassed;
            final check may gate commits.
          </p>
          <ul className="bn-fact-list">
            <li>
              <span>Active</span>
              <code>{tier === "hard" ? "yes" : "no"}</code>
            </li>
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
                ["booking_parse", "cancel_pick", "staff_email"].includes(c.role)
            )
            .map((c, i) => (
              <LlmCallCard key={`hard-llm-${i}`} call={c} defaultOpen={i === 0} />
            ))}
        </>
      );
    case "rag":
      return (
        <>
          <ul className="bn-fact-list">
            <li>
              <span>Active</span>
              <code>{tier === "rag" ? "yes" : "no"}</code>
            </li>
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
          {llmCalls
            .filter((c) => c.role === "rag_generate" || c.role === "gap_polish")
            .map((c, i) => (
              <LlmCallCard key={`rag-llm-${i}`} call={c} defaultOpen={i === 0} />
            ))}
        </>
      );
    case "hko":
      return facts ? (
        <>
          <p className="bn-rules-hint">
            Every hours ask runs an explicit <strong>public-holiday</strong>{" "}
            check on the resolved date and an <strong>HKO severe-weather</strong>{" "}
            check (live signals only when that date is today).
          </p>
          {(facts.checks || []).length > 0 ? (
            <ul className="bn-rule-checks bn-hours-checks">
              {facts.checks.map((c) => (
                <li
                  key={c.id || c.label}
                  className={c.passed ? "pass" : c.checked ? "fail" : "fail"}
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
              <span>Holiday result</span>
              <code>
                {facts.is_holiday
                  ? `YES · ${facts.holiday_name || "public holiday"}`
                  : "NO · not a public holiday"}
              </code>
            </li>
            <li>
              <span>HKO weather result</span>
              <code>
                {facts.weather_hint
                  ? `CLOSURE · ${facts.weather_hint}`
                  : facts.parse_debug?.weather_applied
                    ? "CHECKED · no severe signal today"
                    : facts.parse_debug?.weather_same_day_only
                      ? "SKIPPED · date is not today (same-day live HKO only)"
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
      ) : (
        <p className="bn-empty">
          Ask an hours/date question to populate this panel.
        </p>
      );
    case "chroma":
      return hits.length === 0 ? (
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
            <span>
              top score {scoreLabel(rag?.top_score ?? hits[0]?.score) ?? "—"}
            </span>
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
      );
    case "llm":
      return (
        <>
          <p className="bn-rules-hint">
            Exact system + user text each model call received, plus the raw
            completion.
          </p>
          {history?.preview ? (
            <details className="bn-llm-part bn-llm-context" open>
              <summary>
                Conversation context string
                <em>{String(history.preview).length} chars</em>
              </summary>
              <pre className="bn-pre bn-pre-tall">{history.preview}</pre>
            </details>
          ) : (
            <p className="bn-note">
              No prior turns in session memory for this call.
            </p>
          )}
          {llmCalls.length === 0 ? (
            <p className="bn-empty">
              No DashScope call this turn (deterministic tool / template).
            </p>
          ) : (
            <div className="bn-llm-calls">
              {llmCalls.map((call, i) => (
                <LlmCallCard
                  key={`${call.id || i}-${call.role}`}
                  call={call}
                  defaultOpen={i === llmCalls.length - 1}
                />
              ))}
            </div>
          )}
        </>
      );
    case "sheets":
      return (
        <>
          <p className="bn-rules-hint">
            Side effects written this turn — SheetPro todos, MailBox, chat
            bookings / cancellations CSV.
          </p>
          {!adminTodo &&
          !staffMessage &&
          trace?.booking?.stage !== "booked" &&
          !trace?.cancel?.cancelled ? (
            <p className="bn-empty">No sheet / mailbox writes this turn.</p>
          ) : null}
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
            <details className="bn-llm-part">
              <summary>Cancel / CSV</summary>
              <pre className="bn-pre bn-pre-tall">
                {JSON.stringify(trace.cancel, null, 2)}
              </pre>
            </details>
          ) : null}
        </>
      );
    case "answer":
      return (
        <>
          <ul className="bn-fact-list">
            <li>
              <span>Provider</span>
              <code>{trace.provider || trace.answer_provider || "—"}</code>
            </li>
            <li>
              <span>Model roster</span>
              <code>
                {(trace.llm?.main_model || "—") +
                  (trace.llm?.cheap_called
                    ? ` · cheap ${trace.llm?.cheap_model || ""}`
                    : "")}
              </code>
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
      return <p className="bn-empty">Unknown node.</p>;
  }
}

function FlowNode({ node, lit, active, onOpen }) {
  const pos = LAYOUT[node.id];
  const shape = node.shape;
  return (
    <button
      type="button"
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
      onClick={(e) => {
        e.stopPropagation();
        onOpen(node.id);
      }}
      aria-haspopup="dialog"
      aria-expanded={active}
      title={`${node.label} · ${node.status} — click to inspect`}
    >
      {shape === "decision" ? (
        <span className="bn-flow-diamond-inner">
          <span className="bn-pipe-node-label">{node.label}</span>
          <span className="bn-pipe-node-status">{node.status}</span>
        </span>
      ) : (
        <>
          {shape === "cylinder" ? <span className="bn-cyl-rim" aria-hidden="true" /> : null}
          <span className="bn-pipe-node-label">{node.label}</span>
          <span className="bn-pipe-node-status">{node.status}</span>
          {shape === "cylinder" ? (
            <span className="bn-cyl-base" aria-hidden="true" />
          ) : null}
        </>
      )}
    </button>
  );
}

/**
 * Pan/zoom architecture canvas — Lucid/Mermaid shapes, React-Flow style viewport.
 */
export default function TurnPipeline({
  trace,
  answer = "",
  turnKey = "idle",
  chatHistory = [],
}) {
  const [activeId, setActiveId] = useState(null);
  const [zoom, setZoom] = useState(0.92);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const viewportRef = useRef(null);
  const dragRef = useRef(null);
  const closeBtnRef = useRef(null);
  const fittedRef = useRef(false);

  const route = trace?.route;
  const grounding = trace?.grounding;
  const routing = trace?.routing;
  const steps = trace?.pipeline_steps || [];
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

  const nodes = useMemo(() => {
    const hasTrace = Boolean(trace);
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
        status: !hasTrace
          ? "idle"
          : history?.turns_loaded > 0 || (history?.preview || "").length > 0
            ? "done"
            : "skipped",
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
        label: llmCalls.length
          ? `DashScope · ${llmCalls.length}`
          : "DashScope",
        kind: "llm",
        shape: "process",
        status: !hasTrace ? "idle" : llmCalls.length ? "done" : "skipped",
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
    llmCalls.length,
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
    const path = new Set(["parent", "memory", "router", "tier", "answer"]);
    if (rulesEvaluated.length) path.add("rules");
    if (arbiter?.called) path.add("arbiter");
    if (tier === "soft") {
      path.add("soft");
      if (facts) path.add("hko");
      if (hits.length || enrichMode) path.add("chroma");
      if (llmCalls.length) path.add("llm");
    } else if (tier === "hard") {
      path.add("hard");
      if (llmCalls.length) path.add("llm");
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
      if (llmCalls.length) path.add("llm");
      if (adminTodo) path.add("sheets");
    }
    if (llmCalls.length) path.add("llm");
    return path;
  }, [
    trace,
    rulesEvaluated.length,
    arbiter,
    tier,
    facts,
    hits.length,
    enrichMode,
    llmCalls.length,
    adminTodo,
    staffMessage,
  ]);

  const fitView = useCallback(() => {
    const el = viewportRef.current;
    if (!el) return;
    const { width, height } = el.getBoundingClientRect();
    if (width < 40 || height < 40) return;
    const gw = WORLD.maxX - WORLD.minX;
    const gh = WORLD.maxY - WORLD.minY;
    const pad = 48;
    const nextZoom = Math.min(
      MAX_ZOOM,
      Math.max(MIN_ZOOM, Math.min((width - pad) / gw, (height - pad) / gh))
    );
    const cx = (WORLD.minX + WORLD.maxX) / 2;
    const cy = (WORLD.minY + WORLD.maxY) / 2;
    setZoom(nextZoom);
    setPan({
      x: width / 2 - cx * nextZoom,
      y: height / 2 - cy * nextZoom,
    });
  }, []);

  useEffect(() => {
    setActiveId(null);
    fittedRef.current = false;
  }, [turnKey]);

  useEffect(() => {
    if (fittedRef.current) return;
    const t = window.setTimeout(() => {
      fitView();
      fittedRef.current = true;
    }, 40);
    return () => window.clearTimeout(t);
  }, [fitView, turnKey]);

  useEffect(() => {
    const onResize = () => fitView();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
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

  const onWheel = useCallback((e) => {
    e.preventDefault();
    const el = viewportRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    setZoom((z) => {
      const next = Math.min(
        MAX_ZOOM,
        Math.max(MIN_ZOOM, z * (e.deltaY < 0 ? 1.08 : 0.92))
      );
      setPan((p) => {
        const wx = (mx - p.x) / z;
        const wy = (my - p.y) / z;
        return { x: mx - wx * next, y: my - wy * next };
      });
      return next;
    });
  }, []);

  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return undefined;
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [onWheel]);

  const onPointerDown = (e) => {
    if (e.button !== 0 && e.button !== 1) return;
    if (e.target.closest(".bn-flow-node")) return;
    dragRef.current = {
      x: e.clientX,
      y: e.clientY,
      panX: pan.x,
      panY: pan.y,
      moved: false,
    };
    e.currentTarget.setPointerCapture?.(e.pointerId);
  };

  const onPointerMove = (e) => {
    const d = dragRef.current;
    if (!d) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true;
    setPan({ x: d.panX + dx, y: d.panY + dy });
  };

  const onPointerUp = () => {
    dragRef.current = null;
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
    <div className="bn-pipeline bn-pipeline--canvas" key={turnKey}>
      <header className="bn-pipeline-head bn-pipeline-head--float">
        <div>
          <strong>Request flow</strong>
          <span>{metaLine}</span>
        </div>
        <div className="bn-flow-controls" role="toolbar" aria-label="Canvas controls">
          <button type="button" onClick={() => setZoom((z) => Math.min(MAX_ZOOM, z * 1.12))} title="Zoom in">
            +
          </button>
          <button type="button" onClick={() => setZoom((z) => Math.max(MIN_ZOOM, z / 1.12))} title="Zoom out">
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
                markerWidth="7"
                markerHeight="7"
                orient="auto-start-reverse"
              >
                <path d="M 0 1.2 L 9 5 L 0 8.8 Z" fill="currentColor" />
              </marker>
            </defs>
            {EDGES.map((edge) => {
              const geo = edgeGeometry(edge.from, edge.to);
              if (!geo) return null;
              const lit =
                activePath.has(edge.from) &&
                activePath.has(edge.to) &&
                statusById[edge.from] === "done" &&
                (statusById[edge.to] === "done" ||
                  statusById[edge.to] === "empty");
              return (
                <g key={`${edge.from}-${edge.to}`}>
                  <path
                    d={geo.d}
                    className={`bn-pipe-edge${lit ? " is-lit" : ""}${
                      edge.dashed ? " is-dashed" : ""
                    }`}
                    markerEnd="url(#bn-flow-arrow)"
                  />
                  {edge.label ? (
                    <text
                      x={geo.labelAt.x}
                      y={geo.labelAt.y}
                      className={`bn-pipe-edge-label${lit ? " is-lit" : ""}`}
                      textAnchor="middle"
                    >
                      {edge.label}
                    </text>
                  ) : null}
                </g>
              );
            })}
          </svg>

          <div className="bn-flow-nodes" role="list" aria-label="System architecture">
            {nodes.map((n) => (
              <FlowNode
                key={n.id}
                node={n}
                lit={activePath.has(n.id) && n.status === "done"}
                active={activeId === n.id}
                onOpen={setActiveId}
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
                    chatHistory={chatHistory}
                    route={route}
                    grounding={grounding}
                    routing={routing}
                    steps={steps}
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
