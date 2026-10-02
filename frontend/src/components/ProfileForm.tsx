import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../lib/api";
import type {
  ExampleBucket,
  ProfileFields,
  RecommendationResponse,
  SpendCategory,
  UserProfile,
} from "../lib/types";
import { employmentLabel, preferenceLabel, spendLabel, totalSpend } from "../lib/format";

const EMPTY_SPEND: Record<SpendCategory, number> = {
  fuel: 0,
  dining: 0,
  groceries: 0,
  online_shopping: 0,
  travel: 0,
  utilities: 0,
  other: 0,
};

const BLANK: UserProfile = {
  profile_id: "manual",
  age: 30,
  monthly_income: 60_000,
  employment: "salaried",
  city_tier: 1,
  cibil_score: 740,
  existing_cards: 1,
  missed_payments_12m: 0,
  recent_inquiries_6m: 0,
  utilization_pct: 20,
  monthly_spend: { ...EMPTY_SPEND, travel: 12_000, online_shopping: 8_000, other: 6_000 },
  preferences: ["travel"],
};

/** The form route.
 *
 *  Both routes exist because a demo should not depend on the visitor being able
 *  to describe themselves well. The form goes straight to the same decision
 *  path, so a reviewer can compare the two routes and get the same answer.
 */
export function ProfileForm({
  onResult,
  onProfile,
}: {
  onResult: (result: RecommendationResponse) => void;
  onProfile: (profile: UserProfile | null) => void;
}) {
  const [fields, setFields] = useState<ProfileFields | null>(null);
  const [examples, setExamples] = useState<ExampleBucket | null>(null);
  const [profile, setProfile] = useState<UserProfile>(BLANK);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);

  useEffect(() => {
    api.fields().then(setFields).catch(() => undefined);
    api
      .examples()
      .then((d) => setExamples(d.buckets))
      .catch(() => undefined);
  }, []);

  const spendTotal = useMemo(() => totalSpend(profile.monthly_spend), [profile.monthly_spend]);

  function patch<K extends keyof UserProfile>(key: K, value: UserProfile[K]) {
    setProfile((p) => ({ ...p, [key]: value }));
    onProfile({ ...profile, [key]: value });
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setFieldError(null);
    setBusy(true);
    try {
      const result = await api.recommend(profile);
      onResult(result);
    } catch (err) {
      // A Pydantic rejection is actionable: name the field and say what it
      // wanted, instead of surfacing "422 Unprocessable Entity".
      if (err instanceof ApiError && err.status === 422) {
        setFieldError(err.message);
      } else {
        setError(err instanceof ApiError ? err.message : "Something went wrong.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-8 lg:grid-cols-[1fr_17rem]">
      <form onSubmit={submit} className="min-w-0 space-y-8">
        <Section title="About you">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Age" help="Years.">
              <input
                type="number"
                value={profile.age}
                min={18}
                max={80}
                onChange={(e) => patch("age", Number(e.target.value))}
                className={INPUT}
              />
            </Field>

            <Field label="Monthly income" help="Take-home or gross, either is fine.">
              <input
                type="number"
                value={profile.monthly_income}
                min={0}
                step={1000}
                onChange={(e) => patch("monthly_income", Number(e.target.value))}
                className={INPUT}
              />
            </Field>

            <Field label="Employment">
              <select
                value={profile.employment}
                onChange={(e) => patch("employment", e.target.value)}
                className={INPUT}
              >
                {(fields?.fields.find((f) => f.name === "employment")?.options ?? [
                  "salaried",
                  "self_employed",
                  "business_owner",
                  "student",
                  "retired",
                  "homemaker",
                ]).map((o) => (
                  <option key={o} value={o}>
                    {employmentLabel(o)}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="City tier">
              <select
                value={String(profile.city_tier)}
                onChange={(e) => patch("city_tier", Number(e.target.value) as 1 | 2 | 3)}
                className={INPUT}
              >
                <option value="1">Tier 1 — Mumbai, Delhi, Bengaluru…</option>
                <option value="2">Tier 2 — other metros, state capitals</option>
                <option value="3">Tier 3 — smaller towns</option>
              </select>
            </Field>

            <Field
              label="Credit score"
              help="Leave blank if you have never used credit."
            >
              <input
                type="number"
                value={profile.cibil_score ?? ""}
                min={300}
                max={900}
                placeholder="Not known"
                onChange={(e) =>
                  patch(
                    "cibil_score",
                    e.target.value === "" ? null : Number(e.target.value),
                  )
                }
                className={INPUT}
              />
            </Field>

            <Field label="Card usage" help="Balance as a percentage of your limit.">
              <input
                type="number"
                value={profile.utilization_pct ?? ""}
                min={0}
                max={100}
                placeholder="Not known"
                onChange={(e) =>
                  patch(
                    "utilization_pct",
                    e.target.value === "" ? null : Number(e.target.value),
                  )
                }
                className={INPUT}
              />
            </Field>
          </div>

          <div className="mt-5 grid gap-4 sm:grid-cols-3">
            <Field label="Cards you already hold">
              <input
                type="number"
                min={0}
                value={profile.existing_cards}
                onChange={(e) => patch("existing_cards", Number(e.target.value))}
                className={INPUT}
              />
            </Field>
            <Field label="Missed payments" help="Last 12 months.">
              <input
                type="number"
                min={0}
                value={profile.missed_payments_12m}
                onChange={(e) => patch("missed_payments_12m", Number(e.target.value))}
                className={INPUT}
              />
            </Field>
            <Field label="Recent applications" help="Last 6 months.">
              <input
                type="number"
                min={0}
                value={profile.recent_inquiries_6m}
                onChange={(e) => patch("recent_inquiries_6m", Number(e.target.value))}
                className={INPUT}
              />
            </Field>
          </div>
        </Section>

        <Section
          title="Where you spend each month"
          aside={`${spendTotal.toLocaleString("en-IN")} a month in total`}
        >
          <div className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
            {(fields?.spend_categories ?? (Object.keys(EMPTY_SPEND) as SpendCategory[])).map(
              (cat) => (
                <div key={cat} className="flex items-center gap-3">
                  <label
                    htmlFor={`spend-${cat}`}
                    className="label w-28 shrink-0 text-[0.625rem]"
                  >
                    {spendLabel(cat)}
                  </label>
                  <input
                    id={`spend-${cat}`}
                    type="number"
                    min={0}
                    step={500}
                    value={profile.monthly_spend[cat] ?? 0}
                    onChange={(e) =>
                      setProfile((p) => ({
                        ...p,
                        monthly_spend: {
                          ...p.monthly_spend,
                          [cat]: Number(e.target.value),
                        },
                      }))
                    }
                    className={`${INPUT} num flex-1`}
                  />
                </div>
              ),
            )}
          </div>
        </Section>

        <Section title="What matters to you" help="Optional. Leave all empty and we'll just rank on value.">
          <div className="flex flex-wrap gap-2">
            {(fields?.preferences ?? [
              "cashback",
              "travel",
              "lounge",
              "fuel",
              "lifetime_free",
            ]).map((p) => {
              const on = profile.preferences.includes(p);
              return (
                <button
                  key={p}
                  type="button"
                  aria-pressed={on}
                  onClick={() =>
                    patch(
                      "preferences",
                      on
                        ? profile.preferences.filter((x) => x !== p)
                        : [...profile.preferences, p],
                    )
                  }
                  className={`border px-3 py-1.5 text-[0.8125rem] transition-colors ${
                    on
                      ? "border-ledger bg-ledger text-slip"
                      : "border-rule text-ink-70 hover:border-ledger"
                  }`}
                >
                  {preferenceLabel(p)}
                </button>
              );
            })}
          </div>
        </Section>

        {fieldError ? (
          <p role="alert" className="border-l-2 border-stamp bg-stamp-10 px-3 py-2 text-[0.8125rem]">
            {fieldError}
          </p>
        ) : null}
        {error ? (
          <p role="alert" className="border-l-2 border-stamp bg-stamp-10 px-3 py-2 text-[0.8125rem]">
            {error}
          </p>
        ) : null}

        <div className="flex items-center gap-4">
          <button
            type="submit"
            disabled={busy}
            className="bg-ledger px-6 py-3 font-cond text-sm font-semibold tracking-wide text-slip uppercase hover:bg-ink disabled:opacity-40"
          >
            {busy ? "Checking" : "Show me what fits"}
          </button>
          <p className="text-[0.75rem] text-ink-45">
            Takes about a second. Nothing is stored.
          </p>
        </div>
      </form>

      <aside className="space-y-5">
        <section>
          <h3 className="label rule-heavy pb-2">Or start from an example</h3>
          <p className="mt-2 text-[0.75rem] leading-relaxed text-ink-45">
            Synthetic profiles generated from a fixed seed. No real people.
          </p>
          {examples ? (
            <div className="mt-3 space-y-4">
              {(
                [
                  ["recommended", "Clear fit"],
                  ["borderline", "Close call"],
                  ["rejected", "Not eligible"],
                  ["edge_case", "Edge cases"],
                ] as const
              ).map(([key, heading]) =>
                examples[key]?.length ? (
                  <div key={key}>
                    <p className="label mb-1.5 text-[0.5625rem]">{heading}</p>
                    <ul className="space-y-1">
                      {examples[key].map((ex) => (
                        <li key={ex.profile_id}>
                          <button
                            type="button"
                            onClick={() => {
                              setProfile(ex.profile);
                              onProfile(ex.profile);
                            }}
                            className="num w-full border-l-2 border-rule px-2 py-1 text-left text-[0.6875rem] text-ink-70 hover:border-ledger hover:text-ledger"
                          >
                            {ex.profile_id}
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null,
              )}
            </div>
          ) : (
            <p className="mt-3 text-[0.75rem] text-ink-45">
              Run <code className="num">make data</code> to generate examples.
            </p>
          )}
        </section>
      </aside>
    </div>
  );
}

const INPUT =
  "num w-full border border-rule bg-slip px-3 py-2 text-[0.875rem] text-ink focus:border-ledger focus:outline-none";

function Section({
  title,
  help,
  aside,
  children,
}: {
  title: string;
  help?: string;
  aside?: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="label rule-heavy pb-2">{title}</h2>
        {aside ? <span className="num text-[0.6875rem] text-ink-45">{aside}</span> : null}
      </div>
      {help ? <p className="mt-2 text-[0.75rem] text-ink-45">{help}</p> : null}
      <div className="mt-4">{children}</div>
    </section>
  );
}

function Field({
  label,
  help,
  children,
}: {
  label: string;
  help?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="label block">{label}</span>
      <span className="mt-1.5 block">{children}</span>
      {help ? <span className="mt-1 block text-[0.6875rem] text-ink-45">{help}</span> : null}
    </label>
  );
}
