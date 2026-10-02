import type { RecommendationResponse } from "../lib/types";

/** What just happened, step by step.
 *
 *  Every row is built from the actual response — timings from the trace, counts
 *  from the decision, the model-or-template fact from the verifier report. If a
 *  step has nothing to say (no evidence retrieved, no profile yet), it says so
 *  rather than hiding. A first-time visitor should be able to read this panel
 *  and understand the whole system without opening any other page.
 */
export function PipelineSteps({ result }: { result: RecommendationResponse }) {
  const t = result.trace ?? {};
  const ms = (key: string) =>
    typeof t[key] === "number" ? `${Math.round(t[key]).toLocaleString("en-IN")}ms` : "—";

  const steps: Step[] = [
    {
      name: "Understand you",
      detail: intakeDetail(result),
      time: t["intake"] !== undefined ? ms("intake") : undefined,
    },
    {
      name: "Company-wide checks",
      detail: gateDetail(result),
      time: t["gate"] !== undefined ? ms("gate") : undefined,
    },
    {
      name: "Narrow the catalogue",
      detail:
        t["candidates_returned"] !== undefined
          ? `SQL kept ${Math.round(t["candidates_returned"])} of 120 cards on income, age and score`
          : "Skipped — the company-wide checks already decided this",
      time: t["prefilter"] !== undefined ? ms("prefilter") : undefined,
    },
    {
      name: "Check each card",
      detail:
        t["eligible_count"] !== undefined
          ? `${Math.round(t["eligible_count"])} passed every rule, ${Math.round(t["rejected_count"] ?? 0)} failed at least one — each failure names the exact requirement and how far off it was`
          : "Skipped — nothing survived the earlier steps",
      time: t["eligibility"] !== undefined ? ms("eligibility") : undefined,
    },
    {
      name: "Rank what is left",
      detail: rankDetail(result),
      time: t["rank"] !== undefined ? ms("rank") : undefined,
    },
    {
      name: "Find supporting policy",
      detail:
        result.evidence.length > 0
          ? `${result.evidence.length} passages retrieved: ${result.evidence
              .slice(0, 3)
              .map((e) => e.source)
              .join(", ")}${result.evidence.length > 3 ? ", …" : ""}`
          : "Nothing retrieved — there was nothing to explain",
      time: t["retrieval"] !== undefined ? ms("retrieval") : undefined,
    },
    {
      name: "Write it up",
      detail: explainDetail(result),
      time: t["explain"] !== undefined ? ms("explain") : undefined,
    },
    {
      name: "Verify",
      detail: verifyDetail(result),
    },
  ];

  return (
    <section aria-label="What happened step by step" className="mt-10">
      <h2 className="label rule-heavy pb-2">What happened, step by step</h2>
      <ol className="mt-1">
        {steps.map((s, i) => (
          <li
            key={s.name}
            className="settle grid grid-cols-[2rem_1fr_auto] items-baseline gap-x-3 border-b border-rule py-2.5"
            style={{ "--i": i } as React.CSSProperties}
          >
            <span className="num text-[0.8125rem] text-ink-45">{i + 1}</span>
            <div className="min-w-0">
              <p className="font-cond text-[0.9375rem] font-semibold text-ink">{s.name}</p>
              <p className="mt-0.5 text-[0.8125rem] leading-relaxed text-ink-70">{s.detail}</p>
            </div>
            {s.time ? (
              <span className="num shrink-0 text-[0.6875rem] text-ink-45">{s.time}</span>
            ) : null}
          </li>
        ))}
      </ol>
      <p className="mt-3 text-[0.75rem] leading-relaxed text-ink-45">
        No step here is performed by a language model except “write it up” — and
        even that is checked by the step after it. Total{" "}
        <span className="num">{t["total_ms"] !== undefined ? ms("total_ms") : "—"}</span>.
      </p>
    </section>
  );
}

interface Step {
  name: string;
  detail: string;
  time?: string;
}

function intakeDetail(r: RecommendationResponse): string {
  if (r.status === "need_more_information") {
    const known = r.profile
      ? Object.entries({
          age: r.profile.age,
          income: r.profile.monthly_income,
          employment: r.profile.employment,
          score: r.profile.cibil_score,
        })
          .filter(([, v]) => v !== null && v !== undefined)
          .map(([k, v]) => `${k} ${v}`)
          .join(", ")
      : "";
    return `Not enough yet${known ? ` — understood so far: ${known}` : ""}. Asked one question instead of guessing.`;
  }
  const p = r.profile;
  if (!p) return "A complete profile was supplied directly.";
  return `Profile complete: ${p.age}, ${p.employment.replace(/_/g, " ")}, ₹${p.monthly_income.toLocaleString("en-IN")}/mo${p.cibil_score ? `, CIBIL ${p.cibil_score}` : ", no credit history"}.`;
}

function gateDetail(r: RecommendationResponse): string {
  if (r.rejected_cards.length) {
    return `Failed: ${r.rejected_cards.map((x) => x.label.toLowerCase()).join("; ")}. No card was considered.`;
  }
  return "Passed — old enough, score assessable, no missed payments, applications and usage within limits.";
}

function rankDetail(r: RecommendationResponse): string {
  if (!r.recommendations.length) {
    return r.near_miss.length
      ? `Nothing qualified, so the closest near-misses were kept for advice instead.`
      : "Nothing qualified and nothing was close enough to advise on.";
  }
  const top = r.recommendations[0];
  return `${r.recommendations.length} shown, ordered by a weighted score (net value 55, your spending 20, preferences 15, fee fit 10). Top: ${top.name}, worth about ₹${top.net_annual_value_rs.toLocaleString("en-IN")} a year.`;
}

function explainDetail(r: RecommendationResponse): string {
  if (r.status === "need_more_information") return "Nothing to explain yet — asked the question instead.";
  if (r.verifier?.used_fallback) {
    return "The model was unavailable or its draft failed verification, so this was written from the engine's own figures — plainer, but every number exact.";
  }
  const retries = r.verifier?.retries ?? 0;
  return `Written by the model from the computed facts and retrieved policy${retries > 0 ? ` (took ${retries} ${retries === 1 ? "retry" : "retries"} to pass verification)` : ", passed verification first time"}.`;
}

function verifyDetail(r: RecommendationResponse): string {
  const v = r.verifier;
  if (!v) return "No verification ran for this response.";
  const failed = v.checks.filter((c) => !c.passed);
  if (!failed.length) {
    return `${v.checks.length}/${v.checks.length} checks held: every recommended card passed eligibility, every figure traces to a rule, no invented cards, no personal details leaked.`;
  }
  return `${v.checks.length - failed.length}/${v.checks.length} held. Failed: ${failed.map((c) => c.name).join(", ")}.`;
}
