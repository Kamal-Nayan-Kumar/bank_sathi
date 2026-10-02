import type { SpendMix } from "./types";

/** Rupees, Indian digit grouping. "Rs 1,45,000" not "Rs 145000". */
export function rupees(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const n = Math.round(value);
  if (n >= 100000) {
    const lakh = n / 100000;
    // Two decimals only when it carries information; "Rs 2.5 lakh", not
    // "Rs 2.50 lakh".
    const text = Number.isInteger(lakh)
      ? lakh.toFixed(0)
      : lakh.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
    return `Rs ${text} lakh`;
  }
  return `Rs ${n.toLocaleString("en-IN")}`;
}

export function rupeesExact(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `Rs ${Math.round(value).toLocaleString("en-IN")}`;
}

export function percent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(digits)}%`;
}

export function totalSpend(spend: SpendMix): number {
  return Object.values(spend).reduce((sum, v) => sum + (v || 0), 0);
}

export function spendLabel(category: string): string {
  return category.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

const EMPLOYMENT_LABELS: Record<string, string> = {
  salaried: "Salaried",
  self_employed: "Self-employed",
  business_owner: "Business owner",
  student: "Student",
  retired: "Retired",
  homemaker: "Homemaker",
};

export function employmentLabel(value: string): string {
  return EMPLOYMENT_LABELS[value] ?? value;
}

const PREFERENCE_LABELS: Record<string, string> = {
  cashback: "Cashback",
  travel: "Travel",
  lounge: "Lounge access",
  fuel: "Fuel",
  lifetime_free: "No annual fee",
};

export function preferenceLabel(value: string): string {
  return PREFERENCE_LABELS[value] ?? value;
}

export const TIER_LABELS: Record<string, string> = {
  secured: "Secured",
  entry: "Entry",
  mid: "Mid",
  premium: "Premium",
};

/** Trace keys -> labels. The trace is a debugging surface made visible on
 *  purpose, so it needs to read as English rather than as JSON keys. */
export const TRACE_LABELS: Record<string, string> = {
  intake: "Reading your message",
  gate: "Company-wide checks",
  prefilter: "Narrowing the catalogue",
  candidates_returned: "Cards left after filtering",
  eligibility: "Checking each card",
  eligible_count: "Cards you qualify for",
  rejected_count: "Cards you do not",
  rank: "Ranking",
  retrieval: "Finding supporting policy",
  evidence_chunks: "Policy passages retrieved",
  explain: "Writing the explanation",
  total_ms: "Total",
};

export function fieldLabel(name: string): string {
  const labels: Record<string, string> = {
    age: "Age",
    monthly_income: "Monthly income",
    employment: "Employment",
    city_tier: "City tier",
    cibil_score: "Credit score",
    existing_cards: "Cards you already hold",
    missed_payments_12m: "Missed payments (12 months)",
    recent_inquiries_6m: "Recent applications (6 months)",
    utilization_pct: "Card usage",
    monthly_spend: "Monthly spending",
    max_annual_fee: "Highest annual fee you'd accept",
    preferences: "Preferences",
  };
  return labels[name] ?? name.replace(/_/g, " ");
}
