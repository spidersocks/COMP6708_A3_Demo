import React, { useEffect, useMemo, useState } from "react";

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
        <span className="bn-hit-expand-hint">{open ? "Collapse chunk" : "Expand full chunk"}</span>
      </button>
      {open && body ? <pre className="bn-hit-full">{body}</pre> : null}
    </li>
  );
}

function RuleRow({ rule, isWinner }) {
  const [open, setOpen] = useState(false);
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
        </div>
      </button>
      {open ? (
        <div className="bn-rule-body">
          {reason ? <p className="bn-note">{reason}</p> : null}
          {rule.query ? <pre className="bn-pre">{rule.query}</pre> : null}
        </div>
      ) : null}
    </li>
  );
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

/**
 * Architecture-style live pipeline: click a node to inspect what ran this turn.
 */
export default function TurnPipeline({
  trace,
  answer = "",
  turnKey = "idle",
}) {
  const [activeId, setActiveId] = useState(null);

  const route = trace?.route;
  const grounding = trace?.grounding;
  const routing = trace?.routing;
  const steps = trace?.pipeline_steps || [];
  const llmCalls = Array.isArray(trace?.llm_calls) ? trace.llm_calls : [];
  const history = trace?.history;
  const rag = trace?.rag;
  const facts = trace?.opening_hours?.facts;
  const enrichMode = rag?.mode === "grounded_enrich" || grounding?.used_rag;
  const hits =
    route === "rag" || enrichMode ? rag?.hits || [] : [];
  const rulesEvaluated =
    routing?.rules_evaluated || routing?.debug?.rules_evaluated || [];
  const winningRule = routing?.winning_rule || routing?.debug?.winning_rule || {};
  const matchSummary = routing?.match_summary || routing?.debug?.match_summary || {};
  const arbiter = routing?.debug?.arbiter || {};
  const winningRuleId = matchSummary?.rule_id || winningRule?.route || route;
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
    const list = [
      {
        id: "parent",
        label: "Parent message",
        kind: "start",
        status: hasTrace ? "done" : "idle",
        row: 0,
      },
      {
        id: "memory",
        label: "Session memory",
        kind: "store",
        status: !hasTrace
          ? "idle"
          : history?.turns_loaded > 0 || (history?.preview || "").length > 0
            ? "done"
            : "skipped",
        row: 1,
      },
      {
        id: "router",
        label: "Hybrid router",
        kind: "router",
        status: hasTrace && route ? "done" : hasTrace ? "done" : "idle",
        row: 2,
      },
      {
        id: "rules",
        label: "Rules · scores",
        kind: "rules",
        status: !hasTrace
          ? "idle"
          : rulesEvaluated.length
            ? "done"
            : "skipped",
        row: 3,
      },
      {
        id: "arbiter",
        label: arbiter?.called ? "Route arbiter" : "Arbiter",
        kind: "llm",
        status: !hasTrace
          ? "idle"
          : arbiter?.called
            ? "done"
            : "skipped",
        row: 3,
      },
      {
        id: "tier",
        label:
          tier === "rag"
            ? "Tier · Pure RAG"
            : tier === "hard"
              ? "Tier · Hard FSM"
              : tier === "soft"
                ? "Tier · Live / structured"
                : "Answer tier",
        kind: "branch",
        status: hasTrace && route ? "done" : "idle",
        row: 4,
      },
      {
        id: "soft",
        label: "Live / structured tool",
        kind: "compose",
        status: !hasTrace ? "idle" : tier === "soft" ? "done" : "skipped",
        row: 5,
      },
      {
        id: "hard",
        label: "Hard FSM / hand-off",
        kind: "hard",
        status: !hasTrace ? "idle" : tier === "hard" ? "done" : "skipped",
        row: 5,
      },
      {
        id: "rag",
        label: "Pure RAG",
        kind: "rag",
        status: !hasTrace ? "idle" : tier === "rag" ? "done" : "skipped",
        row: 5,
      },
      {
        id: "hko",
        label: "HKO · holiday",
        kind: "live",
        status: !hasTrace
          ? "idle"
          : facts
            ? "done"
            : "skipped",
        row: 6,
      },
      {
        id: "chroma",
        label: "Chroma KB",
        kind: "store",
        status: !hasTrace
          ? "idle"
          : hits.length || rag?.hit_count
            ? "done"
            : tier === "rag" || enrichMode
              ? "empty"
              : "skipped",
        row: 6,
      },
      {
        id: "llm",
        label: llmCalls.length
          ? `DashScope · ${llmCalls.length}`
          : "DashScope",
        kind: "llm",
        status: !hasTrace
          ? "idle"
          : llmCalls.length
            ? "done"
            : "skipped",
        row: 7,
      },
      {
        id: "sheets",
        label: "SheetPro · Mail · CSV",
        kind: "store",
        status: !hasTrace
          ? "idle"
          : adminTodo ||
              staffMessage ||
              trace?.booking?.stage === "booked" ||
              trace?.cancel?.stage === "cancelled"
            ? "done"
            : "skipped",
        row: 7,
      },
      {
        id: "answer",
        label: "Parent answer",
        kind: "end",
        status: hasTrace ? "done" : "idle",
        row: 8,
      },
    ];
    return list;
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

  useEffect(() => {
    setActiveId(null);
  }, [turnKey]);

  useEffect(() => {
    if (!trace) return;
    // Default focus: LLM if called, else answer tier / router
    if (llmCalls.length) setActiveId("llm");
    else if (route) setActiveId("tier");
    else setActiveId("router");
  }, [turnKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const active = nodes.find((n) => n.id === activeId) || null;

  const rows = useMemo(() => {
    const byRow = new Map();
    for (const n of nodes) {
      const r = n.row;
      if (!byRow.has(r)) byRow.set(r, []);
      byRow.get(r).push(n);
    }
    return Array.from(byRow.entries()).sort((a, b) => a[0] - b[0]);
  }, [nodes]);

  function renderDetail() {
    if (!trace) {
      return (
        <p className="bn-empty">
          Send a parent message to light up the pipeline. Click any node after a
          turn to inspect inputs, rules, retrieval, and model I/O.
        </p>
      );
    }
    if (!active) {
      return <p className="bn-empty">Select a node in the diagram.</p>;
    }
    switch (active.id) {
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
                <code>{trace.latency_ms != null ? `${trace.latency_ms} ms` : "—"}</code>
              </li>
            </ul>
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
      case "memory":
        return (
          <>
            <ul className="bn-fact-list">
              <li>
                <span>Turns loaded</span>
                <code>
                  {history?.turns_loaded ?? (history?.turns || []).length ?? 0}/
                  {history?.limit ?? "—"}
                </code>
              </li>
              <li>
                <span>Enabled</span>
                <code>{history?.enabled ? "yes" : "no"}</code>
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
              <p className="bn-empty">No prior turns in session memory.</p>
            )}
            {(history?.turns || []).length > 0 ? (
              <ul className="bn-mem-turns">
                {history.turns.map((t, i) => (
                  <li key={`mem-${i}`}>
                    <details>
                      <summary>
                        <strong>
                          {t.role === "user" || t.role === "parent" ? "parent" : "bot"}
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
            {matchSummary?.headline ? (
              <p className="bn-match-headline">{matchSummary.headline}</p>
            ) : null}
            {matchSummary?.cue ? (
              <p className="bn-note">
                Cue «{matchSummary.cue}» → <code>{matchSummary.route || route}</code>
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
              evidence.
            </p>
            {routing?.scores && Object.keys(routing.scores).length > 0 ? (
              <ul className="bn-fact-list">
                {Object.entries(routing.scores)
                  .sort((a, b) => Number(b[1]) - Number(a[1]))
                  .slice(0, 10)
                  .map(([k, v]) => (
                    <li key={k}>
                      <span>{k}</span>
                      <code>{Number(v).toFixed(3)}</code>
                    </li>
                  ))}
              </ul>
            ) : null}
            {rulesEvaluated.length > 0 ? (
              <ul className="bn-rule-list">
                {rulesEvaluated.map((r) => (
                  <RuleRow
                    key={r.id}
                    rule={r}
                    isWinner={r.id === winningRuleId && Boolean(r.matched)}
                  />
                ))}
              </ul>
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
            </ul>
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
              <pre className="bn-pre">{JSON.stringify(finalCheck, null, 2)}</pre>
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
                <code>{scoreLabel(rag?.top_score ?? hits[0]?.score) ?? "—"}</code>
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
              Public-holiday check on the resolved date; live HKO severe-weather
              only when that date is today.
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
                    : "NO"}
                </code>
              </li>
              <li>
                <span>HKO</span>
                <code>
                  {facts.weather_hint
                    ? `CLOSURE · ${facts.weather_hint}`
                    : facts.parse_debug?.weather_applied
                      ? "CHECKED · clear"
                      : facts.parse_debug?.weather_same_day_only
                        ? "SKIPPED · not today"
                        : "not run"}
                </code>
              </li>
            </ul>
          </>
        ) : (
          <p className="bn-empty">No hours / holiday / HKO check on this turn.</p>
        );
      case "chroma":
        return hits.length === 0 ? (
          <p className="bn-empty">
            {tier === "rag" || enrichMode
              ? "No chunks returned."
              : "KB retrieve did not run (hard / tool path)."}
          </p>
        ) : (
          <>
            <div className="bn-rag-strip">
              <span>index {rag?.index_size ?? "—"}</span>
              <span>top_k {rag?.top_k ?? hits.length}</span>
              <span>hits {rag?.hit_count ?? hits.length}</span>
              <span>top {scoreLabel(rag?.top_score ?? hits[0]?.score) ?? "—"}</span>
              <span>{rag?.embedding_mode || "—"}</span>
            </div>
            <ul className="bn-hits">
              {hits.map((h, i) => (
                <RagHit key={`${h.source}-${h.rank || i}`} hit={h} index={i} />
              ))}
            </ul>
          </>
        );
      case "llm":
        return llmCalls.length === 0 ? (
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
            </ul>
            <pre className="bn-pre bn-pre-tall">{answer || "(empty)"}</pre>
          </>
        );
      default:
        return <p className="bn-empty">Unknown node.</p>;
    }
  }

  return (
    <div className="bn-pipeline" key={turnKey}>
      <div className="bn-pipeline-head">
        <div>
          <strong>Turn pipeline</strong>
          <span>
            {trace
              ? `${route || "—"} · ${trace.latency_ms != null ? `${trace.latency_ms} ms` : "—"}`
              : "Idle — waiting for a parent message"}
          </span>
        </div>
      </div>

      <div className="bn-pipeline-layout">
        <div className="bn-pipeline-graph" role="list" aria-label="Turn architecture">
          {rows.map(([rowIdx, rowNodes], ri) => (
            <React.Fragment key={`row-${rowIdx}`}>
              {ri > 0 ? (
                <div className="bn-pipeline-arrow" aria-hidden="true">
                  ↓
                </div>
              ) : null}
              <div
                className={`bn-pipeline-row${
                  rowNodes.length > 1 ? " bn-pipeline-row-split" : ""
                }`}
              >
                {rowNodes.map((n) => (
                  <button
                    key={n.id}
                    type="button"
                    role="listitem"
                    className={`bn-pipe-node bn-pipe-${n.kind} bn-pipe-${n.status}${
                      activeId === n.id ? " is-active" : ""
                    }`}
                    onClick={() =>
                      setActiveId((cur) => (cur === n.id ? null : n.id))
                    }
                  >
                    <span className="bn-pipe-node-label">{n.label}</span>
                    <span className="bn-pipe-node-status">{n.status}</span>
                  </button>
                ))}
              </div>
            </React.Fragment>
          ))}
        </div>

        <aside className="bn-pipeline-detail" aria-live="polite">
          <div className="bn-pipeline-detail-head">
            <strong>{active?.label || "Inspector"}</strong>
            {active ? (
              <span className={`bn-pipe-pill bn-pipe-${active.status}`}>
                {active.status}
              </span>
            ) : null}
          </div>
          <div className="bn-pipeline-detail-body">{renderDetail()}</div>
        </aside>
      </div>
    </div>
  );
}
