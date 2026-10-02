import { useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import type {
  Example,
  ExampleBucket,
  RecommendationResponse,
  UserProfile,
} from "../lib/types";
import { rupees } from "../lib/format";

const GROUPS = [
  ["recommended", "Clear fit"],
  ["borderline", "Close call"],
  ["rejected", "Not eligible"],
  ["edge_case", "Edge cases"],
] as const;

/** Scenario one: pick a sample customer, see what the engine does with them.
 *
 *  Profiles are synthetic and generated from a fixed seed. The bucket they sit
 *  in is computed from the engine's own ground truth, so a "close call" is
 *  actually close and "not eligible" actually fails.
 */
export function SampleProfiles({
  onResult,
  onPick,
}: {
  onResult: (result: RecommendationResponse) => void;
  onPick: (profile: UserProfile | null) => void;
}) {
  const [buckets, setBuckets] = useState<ExampleBucket | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .examples()
      .then((d) => setBuckets(d.buckets))
      .catch(() => setError("Could not load the sample profiles."));
  }, []);

  async function choose(ex: Example) {
    setActive(ex.profile_id);
    setError(null);
    onPick(ex.profile);
    try {
      onResult(await api.recommend(ex.profile));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setActive(null);
    }
  }

  if (error && !buckets) {
    return (
      <p role="alert" className="border-l-2 border-stamp bg-stamp-10 px-3 py-2 text-[0.8125rem]">
        {error}
      </p>
    );
  }

  return (
    <div className="space-y-8">
      {GROUPS.map(([key, heading]) =>
        buckets?.[key]?.length ? (
          <section key={key} aria-label={heading}>
            <h2 className="label rule-heavy pb-2">{heading}</h2>
            <ul className="mt-3 grid gap-3 sm:grid-cols-2">
              {buckets[key].map((ex) => (
                <li key={ex.profile_id}>
                  <button
                    type="button"
                    onClick={() => void choose(ex)}
                    disabled={active !== null}
                    aria-label={`Check ${describe(ex)}`}
                    className="group flex w-full flex-col gap-1 border border-rule bg-slip px-4 py-3 text-left transition-colors hover:border-ledger disabled:opacity-60"
                  >
                    <span className="flex items-baseline justify-between gap-3">
                      <span className="font-cond text-[1.0625rem] font-semibold tracking-tight text-ink">
                        {label(ex)}
                      </span>
                      <span className="num shrink-0 text-[0.6875rem] text-ink-45">
                        {active === ex.profile_id ? "Checking…" : "Check →"}
                      </span>
                    </span>
                    <span className="num text-[0.75rem] text-ink-70">
                      {facts(ex.profile)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ) : null,
      )}
      {!buckets ? (
        <p className="label">Loading the sample customers…</p>
      ) : null}
      {error ? (
        <p role="alert" className="border-l-2 border-stamp bg-stamp-10 px-3 py-2 text-[0.8125rem]">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** One-line summary of who this customer is, in their terms.
 *
 *  "other" is skipped when naming the top category: generated profiles park
 *  uncategorised spend there, so "mostly other" would be true and useless.
 */
function facts(p: UserProfile): string {
  const bits = [
    `${p.age}`,
    rupees(p.monthly_income).replace(" lakh", "L").replace("Rs ", "₹") + "/mo",
    p.cibil_score ? `CIBIL ${p.cibil_score}` : "no credit history",
  ];
  const named = Object.entries(p.monthly_spend)
    .filter(([cat]) => cat !== "other")
    .sort((a, b) => b[1] - a[1])[0];
  if (named && named[1] > 0) bits.push(`spends on ${named[0].replace(/_/g, " ")}`);
  return bits.join(" · ");
}

function describe(ex: Example): string {
  return `${label(ex)}: ${facts(ex.profile)}`;
}

const EDGE_GLOSS: Record<string, string> = {
  EDGE_INCOME_EXACT: "Income exactly on a card minimum",
  EDGE_INCOME_MINUS1: "One rupee under it",
  EDGE_INCOME_PLUS1: "One rupee over it",
  EDGE_CIBIL_EXACT: "Score exactly on a card minimum",
  EDGE_CIBIL_MINUS1: "One point under it",
  EDGE_AGE_AT_MIN: "Age exactly at a card minimum",
  EDGE_AGE_MINUS1: "One year under it",
  EDGE_AGE_AT_MAX: "Age exactly at a card maximum",
  EDGE_AGE_MAXPLUS1: "One year over it",
  EDGE_GATE_CIBIL_MIN: "Score on the company floor",
  EDGE_GATE_CIBIL_MINUS1: "One point under it",
  EDGE_GATE_ONE_MISSED: "A single missed payment on file",
  EDGE_GATE_INQUIRIES_MAX: "Applications at the limit",
  EDGE_GATE_INQUIRIES_OVER: "One application over the limit",
  EDGE_UTILISATION_MAX: "Usage exactly at the limit",
  EDGE_UTILISATION_OVER: "Usage just over it",
  EDGE_NEW_TO_CREDIT: "No credit history at all",
  EDGE_NEW_TO_CREDIT_AT_THRESHOLD: "Score on the new-to-credit line",
  EDGE_NO_ELIGIBLE: "Qualifies for almost nothing",
  EDGE_CONTRADICTORY: "Wants lounge access on a small budget",
  EDGE_HIGH_INCOME_LOW_SPEND: "Earns a lot, spends almost nothing",
  EDGE_NO_SPEND: "No card spending at all",
};

/** A human name for the profile, so nobody has to read USER_0038. */
function label(ex: Example): string {
  if (ex.profile_id.startsWith("EDGE_")) {
    return EDGE_GLOSS[ex.profile_id] ?? ex.profile_id;
  }
  const p = ex.profile;
  if (p.employment === "student" || p.cibil_score === null) return "New to credit";
  if (p.age < 27) return "Early career";
  if ((p.cibil_score ?? 800) < 700) return "Rebuilding a score";
  if (p.monthly_income >= 150_000) return "High earner";
  const top = Object.entries(p.monthly_spend).sort((a, b) => b[1] - a[1])[0]?.[0];
  if (top === "travel") return "Frequent flyer";
  if (top === "fuel") return "Daily commuter";
  if (top === "online_shopping") return "Online shopper";
  if (top === "dining") return "Dines out";
  return "Everyday spender";
}
