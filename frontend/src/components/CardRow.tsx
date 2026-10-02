import { useState } from "react";
import type { Recommendation, ScoreBreakdown } from "../lib/types";
import { rupees, rupeesExact, percent, TIER_LABELS } from "../lib/format";
import { ClearanceBlock } from "./ClearanceBlock";

/** One recommended card, as a ledger row.
 *
 *  Rank sits in the margin as a small mono index rather than a display
 *  numeral: rank is an ordering, but these are not steps in a process, and
 *  shouting "01" would imply a sequence that does not exist.
 */
export function CardRow({
  card,
  breakdown,
  index,
}: {
  card: Recommendation;
  breakdown?: ScoreBreakdown;
  index: number;
}) {
  const [open, setOpen] = useState(false);
  const free = card.fee_payable_rs === 0;

  return (
    <li
      className="settle slip rule"
      style={{ "--i": index } as React.CSSProperties}
    >
      <div className="grid grid-cols-[2.25rem_1fr] gap-x-4 px-4 py-5 sm:px-6">
        {/* margin index */}
        <div className="num text-lg leading-none text-ink-45 tabular-nums">
          {card.rank}
        </div>

        <div className="min-w-0 space-y-4">
          {/* --- header line ------------------------------------------- */}
          <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
            <div className="min-w-0">
              <h3 className="font-cond text-xl leading-tight font-semibold tracking-tight text-ink">
                {card.name}
              </h3>
              <p className="label mt-0.5">
                {card.bank} · {TIER_LABELS[card.tier] ?? card.tier} ·{" "}
                <span className="num">{card.card_id}</span>
              </p>
            </div>

            {/* net value is the headline figure: rupees, not a score */}
            <div className="text-right">
              <div className="num text-2xl leading-none text-ledger">
                {rupees(card.net_annual_value_rs)}
              </div>
              <p className="label mt-1 text-[0.5625rem]">value over a year</p>
            </div>
          </div>

          {/* --- the figures, as a passbook column --------------------- */}
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 border-y border-rule py-3 sm:grid-cols-4">
            <Cell
              label={free ? "Annual fee" : "Fee you'd pay"}
              value={free ? "Waived" : rupeesExact(card.fee_payable_rs)}
              tone={free ? "good" : undefined}
            />
            <Cell label="APR" value={percent(card.apr_pct)} />
            <Cell
              label="Limit, roughly"
              value={rupees(card.est_credit_limit_rs)}
            />
            <Cell label="Match score" value={`${(card.score * 100).toFixed(0)}%`} />
          </dl>

          <ClearanceBlock breakdown={breakdown} score={card.score} />

          {card.key_benefits.length ? (
            <ul className="flex flex-wrap gap-x-5 gap-y-1">
              {card.key_benefits.map((b) => (
                <li
                  key={b}
                  className="flex items-baseline gap-2 text-[0.8125rem] text-ink-70"
                >
                  <span
                    aria-hidden
                    className="size-1 shrink-0 translate-y-[-2px] bg-ledger"
                  />
                  {b}
                </li>
              ))}
            </ul>
          ) : null}

          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="label inline-flex items-center gap-1.5 text-ledger hover:text-ink"
          >
            {open ? "Hide the workings" : "Show the workings"}
            <span aria-hidden className="text-[0.5rem]">
              {open ? "▲" : "▼"}
            </span>
          </button>

          {open ? <Workings card={card} breakdown={breakdown} /> : null}
        </div>
      </div>
    </li>
  );
}

function Workings({
  card,
  breakdown,
}: {
  card: Recommendation;
  breakdown?: ScoreBreakdown;
}) {
  return (
    <div className="slip border-l-2 border-ledger bg-paper/60 px-4 py-3 text-[0.8125rem] text-ink-70">
      <p className="label mb-2">How this score was reached</p>
      <ol className="space-y-1.5">
        {breakdown ? (
          <>
            <Row
              k="Net value"
              v={`rewards minus the fee you'd pay, over a year = ${rupeesExact(card.net_annual_value_rs)}`}
            />
            <Row
              k="Spend alignment"
              v="how well the card rewards the categories you actually spend in"
            />
            <Row
              k="Preference match"
              v="whether it does what you said you wanted"
            />
            <Row k="Fee fit" v="whether the fee is comfortable against what you get back" />
          </>
        ) : (
          <li>Breakdown not returned for this card.</li>
        )}
        <Row
          k="Sources"
          v={card.sources.length ? card.sources.join(", ") : "catalogue"}
        />
      </ol>
      <p className="mt-2 text-[0.75rem] text-ink-45">
        The limit is an estimate for comparing cards, not a sanctioned limit.
      </p>
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <li className="flex gap-3">
      <span className="label w-32 shrink-0 pt-0.5 text-[0.5625rem]">{k}</span>
      <span className="flex-1">{v}</span>
    </li>
  );
}

function Cell({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "good";
}) {
  return (
    <div>
      <dt className="label text-[0.5625rem]">{label}</dt>
      <dd
        className={`num mt-0.5 text-sm ${
          tone === "good" ? "text-ledger" : "text-ink"
        }`}
      >
        {value}
      </dd>
    </div>
  );
}
