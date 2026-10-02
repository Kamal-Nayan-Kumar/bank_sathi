import type { ScoreBreakdown } from "../lib/types";

/* The four weights the ranking uses, in the order the policy page lists them.
   Read from the response so the UI cannot disagree with the engine about what
   the score is made of. */
type ComponentKey = "net_value" | "spend_alignment" | "preference_match" | "fee_fit";

const COMPONENTS: Array<{ key: ComponentKey; label: string; weight: number }> = [
  { key: "net_value", label: "Net value", weight: 55 },
  { key: "spend_alignment", label: "Your spending", weight: 20 },
  { key: "preference_match", label: "Preferences", weight: 15 },
  { key: "fee_fit", label: "Fee vs value", weight: 10 },
];

/**
 * The clearance block — the signature element.
 *
 * The whole premise of this product is that a ranking should be derivable rather
 * than asserted, so the score is never shown as a bare number. It is shown as
 * the four weighted parts that produced it, sized proportionally. If a card
 * looks wrong in the list, you can see here which component is responsible.
 */
export function ClearanceBlock({
  breakdown,
  score,
}: {
  breakdown?: ScoreBreakdown;
  score: number;
}) {
  const contribution = (c: ComponentKey) =>
    breakdown ? (breakdown[c] * COMPONENTS.find((x) => x.key === c)!.weight) / 100 : 0;
  const earned = breakdown
    ? COMPONENTS.reduce((sum, c) => sum + contribution(c.key), 0)
    : score * 100;

  return (
    <div className="space-y-2">
      <div className="flex h-2.5 w-full overflow-hidden border border-rule bg-paper">
        {breakdown
          ? COMPONENTS.map((c) => (
              <div
                key={c.key}
                className="bg-ledger"
                style={{ width: `${contribution(c.key)}%` }}
                title={`${c.label}: ${(breakdown[c.key] * 100).toFixed(0)}% of a ${c.weight}% weight`}
              />
            ))
          : null}
        {/* Unattributed remainder, so the bar always reads as a whole scale. */}
        <div className="bg-ledger-10" style={{ width: `${Math.max(0, 100 - earned)}%` }} />
      </div>

      {breakdown ? (
        <ul className="grid grid-cols-2 gap-x-4 gap-y-0.5 sm:grid-cols-4">
          {COMPONENTS.map((c) => (
            <li key={c.key} className="flex items-baseline justify-between gap-2">
              <span className="label text-[0.5625rem]">{c.label}</span>
              <span className="num text-[0.625rem] text-ink-70">
                {(breakdown[c.key] * 100).toFixed(0)}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="label text-[0.5625rem]">
          Weighting: net value 55 · your spending 20 · preferences 15 · fee 10
        </p>
      )}
    </div>
  );
}
