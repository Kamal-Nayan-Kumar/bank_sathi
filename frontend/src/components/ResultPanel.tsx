import type { RecommendationResponse, VerifierReport } from "../lib/types";
import { CardRow } from "./CardRow";
import { TraceList } from "./StatusStrip";

const VERIFIER_LABELS: Record<string, string> = {
  eligibility_consistency: "every recommended card passed eligibility",
  no_approval_language_when_rejected: "no approval promised on a rejection",
  ranking_order: "cards are in score order",
  known_card_ids: "no invented cards",
  no_invented_card_names: "no invented card names",
  no_unsupported_numbers: "every figure traces to a rule",
  no_pii_in_output: "no personal details in the output",
  no_internal_reason_codes: "no internal codes exposed",
  has_evidence: "explanation is backed by policy",
  has_a_summary: "there is an explanation to read",
  has_a_question: "the follow-up asks something",
  status_consistent: "outcome label matches the result",
  llm_grounding: "model judge found the summary supported",
};

/** The verifier badge.
 *
 *  Shown whether it passed or failed. A green badge you can always see is
 *  decoration; a badge that can turn red is evidence that the check is real,
 *  and the customer deserves to know when the text they are reading was
 *  repaired rather than generated.
 */
export function VerifierBadge({
  verifier,
}: {
  verifier: VerifierReport | null;
}) {
  if (!verifier) return null;
  const failed = verifier.checks.filter((c) => !c.passed);
  const ok = verifier.passed;

  return (
    <div
      className={`stamp-in w-max max-w-full border px-3 py-2 ${
        ok ? "border-ledger/30 bg-ledger-10/50" : "border-stamp/30 bg-stamp-10"
      }`}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span
          className={`label ${ok ? "text-ledger" : "text-stamp"}`}
          style={{ transform: "rotate(-1.5deg)" }}
        >
          {ok ? "Checked ✓" : "Repaired"}
        </span>
        <span className="num text-[0.6875rem] text-ink-70">
          {verifier.checks.length - failed.length}/{verifier.checks.length} checks
          {verifier.retries > 0
            ? ` · ${verifier.retries} retr${verifier.retries > 1 ? "ies" : "y"}`
            : ""}
        </span>
      </div>

      {failed.length ? (
        <ul className="mt-2 space-y-1">
          {failed.map((c) => (
            <li key={c.name} className="text-[0.8125rem] text-ink-70">
              <span className="text-stamp">✕</span>{" "}
              {VERIFIER_LABELS[c.name] ?? c.name}
              {c.detail ? <span className="text-ink-45"> — {c.detail}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}

      {verifier.used_fallback ? (
        <p className="mt-2 text-[0.75rem] text-ink-45">
          Written from the engine&apos;s figures rather than by the model, so
          nothing here is paraphrased.
        </p>
      ) : null}
    </div>
  );
}

/** The full result panel: what was decided, on what basis, and what to do next. */
export function ResultPanel({ result }: { result: RecommendationResponse }) {
  const { status } = result;

  return (
    <section aria-live="polite" className="space-y-6">
      <Summary result={result} />
      <VerifierBadge verifier={result.verifier} />

      {status === "need_more_information" ? null : (
        <>
          {result.recommendations.length ? (
            <section aria-labelledby="recs-heading" className="space-y-0">
              <h2 id="recs-heading" className="label rule-heavy pb-2">
                Your options, in order
              </h2>
              <ul>
                {result.recommendations.map((card, i) => (
                  <CardRow
                    key={card.card_id}
                    card={card}
                    index={i}
                    breakdown={result.score_breakdown.find(
                      (b) => b.card_id === card.card_id,
                    )}
                  />
                ))}
              </ul>
            </section>
          ) : null}

          <Failures result={result} />

          {result.trace && Object.keys(result.trace).length ? (
            <details className="group">
              <summary className="label cursor-pointer list-none text-ledger hover:text-ink">
                <span className="group-open:hidden">Show what ran, and how long it took</span>
                <span className="hidden group-open:inline">
                  Hide what ran
                </span>
              </summary>
              <div className="slip mt-2 border border-rule px-4 py-3">
                <TraceList trace={result.trace} />
              </div>
            </details>
          ) : null}
        </>
      )}
    </section>
  );
}

function Summary({ result }: { result: RecommendationResponse }) {
  const tone =
    result.status === "need_more_information"
      ? "text-ochre"
      : result.status === "recommendations_available"
        ? "text-ledger"
        : "text-stamp";

  return (
    <div className="space-y-2">
      <h2 className={`font-cond text-[0.6875rem] font-semibold tracking-[0.12em] uppercase ${tone}`}>
        {result.status === "need_more_information"
          ? "One more thing"
          : result.decision === "recommended"
            ? "Here is what fits"
            : "Not yet"}
      </h2>
      <p className="text-[1.0625rem] leading-relaxed text-ink">{result.summary}</p>
      {result.status === "need_more_information" && result.missing_fields.length ? (
        <p className="label">Still needed</p>
      ) : null}
    </div>
  );
}

function Failures({ result }: { result: RecommendationResponse }) {
  if (
    !result.rejected_cards.length &&
    !result.near_miss.length &&
    !result.improvement_steps.length &&
    !result.rejections.length
  ) {
    return null;
  }

  return (
    <div className="grid gap-6 md:grid-cols-2">
      {result.rejected_cards.length ? (
        <section aria-labelledby="blocked-heading">
          <h3 id="blocked-heading" className="label rule-heavy pb-2 text-stamp">
            Why we could not recommend anything
          </h3>
          <ul className="mt-3 space-y-3">
            {result.rejected_cards.map((r) => (
              <li key={r.code} className="flex gap-3 text-[0.875rem] text-ink">
                <span aria-hidden className="mt-1 text-stamp">
                  ✕
                </span>
                <span>
                  <span className="font-semibold">{r.label}. </span>
                  {r.message}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {result.near_miss.length ? (
        <section aria-labelledby="near-heading">
          <h3 id="near-heading" className="label rule-heavy pb-2 text-ochre">
            You were close on these
          </h3>
          <ul className="mt-3 space-y-2">
            {result.near_miss.map((ev) => (
              <li key={ev.card_id} className="text-[0.875rem]">
                <span className="num font-semibold">{ev.card_id}</span>
                <span className="text-ink-70">
                  {" — "}
                  {ev.reasons.map((r) => r.message).join(" ")}
                </span>
              </li>
            ))}
          </ul>
          {result.improvement_steps.length ? (
            <ul className="mt-3 space-y-2">
              {result.improvement_steps.map((s) => (
                <li key={s} className="flex gap-3 text-[0.875rem] text-ink-70">
                  <span aria-hidden className="mt-1 text-ledger">
                    →
                  </span>
                  {s}
                </li>
              ))}
            </ul>
          ) : null}
        </section>
      ) : result.improvement_steps.length ? (
        <section aria-labelledby="improve-heading">
          <h3 id="improve-heading" className="label rule-heavy pb-2 text-ochre">
            What would help
          </h3>
          <ul className="mt-3 space-y-2">
            {result.improvement_steps.map((s) => (
              <li key={s} className="flex gap-3 text-[0.875rem] text-ink-70">
                <span aria-hidden className="mt-1 text-ledger">
                  →
                </span>
                {s}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {result.rejections.length ? (
        <section aria-labelledby="rejections-heading" className="md:col-span-2">
          <details className="group">
            <summary className="label cursor-pointer list-none text-ledger hover:text-ink">
              <span className="group-open:hidden">
                See the cards you did not qualify for ({result.rejections.length})
              </span>
              <span className="hidden group-open:inline">
                Hide the cards you did not qualify for
              </span>
            </summary>
            <div className="mt-3 overflow-x-auto">
              <table className="w-full min-w-[34rem] border-collapse text-left text-[0.8125rem]">
                <thead>
                  <tr className="rule-heavy">
                    <th className="label py-1.5 pr-4 text-[0.5625rem]">Card</th>
                    <th className="label py-1.5 pr-4 text-[0.5625rem]">Requirement</th>
                    <th className="label py-1.5 text-[0.5625rem]">Yours</th>
                  </tr>
                </thead>
                <tbody>
                  {result.rejections.flatMap((ev) =>
                    ev.reasons.map((r) => (
                      <tr key={`${ev.card_id}-${r.code}`} className="rule align-top">
                        <td className="num py-2 pr-4 whitespace-nowrap">
                          {ev.card_id}
                        </td>
                        <td className="py-2 pr-4 text-ink">{r.message}</td>
                        <td className="num py-2 whitespace-nowrap text-ink-45">
                          {formatFigure(r.actual)}
                        </td>
                      </tr>
                    )),
                  )}
                </tbody>
              </table>
            </div>
          </details>
        </section>
      ) : null}
    </div>
  );
}

function formatFigure(value: number | string | null): string {
  if (value === null || value === undefined) return "—";
  return typeof value === "number" ? value.toLocaleString("en-IN") : String(value);
}
