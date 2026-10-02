import { useEffect, useState } from "react";
import { api } from "./lib/api";
import type { HealthStatus, RecommendationResponse, UserProfile } from "./lib/types";
import { StatusStrip } from "./components/StatusStrip";
import { SampleProfiles } from "./components/SampleProfiles";
import { ChatPanel } from "./components/ChatPanel";
import { ProfileForm } from "./components/ProfileForm";
import { ResultPanel } from "./components/ResultPanel";

type Scenario = "samples" | "yours";
type Input = "chat" | "form";

/** Two scenarios, kept apart on purpose.
 *
 *  "Sample profiles" is the guided tour: pick a customer, watch the engine work.
 *  "Your details" is the real thing: describe yourself or fill the form.
 *  Both land on the same result panel, because they run the same decision path.
 */
export default function App() {
  const [scenario, setScenario] = useState<Scenario>("samples");
  const [input, setInput] = useState<Input>("chat");
  const [result, setResult] = useState<RecommendationResponse | null>(null);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [profile, setProfile] = useState<UserProfile | null>(null);

  useEffect(() => {
    let cancelled = false;
    // One retry: the API is often still warming its model when the page loads,
    // and a single failed fetch would otherwise leave the strip blank forever.
    api.health().then(setHealth).catch(() => {
      setTimeout(() => {
        if (!cancelled) api.health().then(setHealth).catch(() => undefined);
      }, 4000);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  function reset() {
    setResult(null);
  }

  return (
    <div className="min-h-dvh">
      {/* One container width for everything. The header, the work area and the
          footer all share it, so nothing on the page can drift out of line. */}
      <div className="mx-auto w-full max-w-5xl px-5 sm:px-8">
        <header className="flex flex-wrap items-end justify-between gap-x-8 gap-y-4 pt-10 pb-6">
          <div>
            <h1 className="font-cond text-4xl leading-none font-bold tracking-tight text-ink sm:text-5xl">
              Card <span className="text-ledger">Sathi</span>
            </h1>
            <p className="mt-2 text-[0.9375rem] text-ink-70">
              The right card, with the workings shown.
            </p>
          </div>

          <nav aria-label="Scenarios" className="flex border border-rule bg-slip">
            {(
              [
                ["samples", "Sample profiles"],
                ["yours", "Your details"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => {
                  setScenario(value);
                  reset();
                }}
                aria-current={scenario === value}
                className={`px-4 py-2.5 font-cond text-[0.8125rem] font-semibold tracking-wide uppercase transition-colors ${
                  scenario === value
                    ? "bg-ledger text-slip"
                    : "text-ink-70 hover:bg-paper hover:text-ink"
                }`}
              >
                {label}
              </button>
            ))}
          </nav>
        </header>

        {scenario === "yours" && !result ? (
          <div className="mb-6 flex w-max border border-rule bg-slip" role="group" aria-label="How to give your details">
            {(
              [
                ["chat", "Describe in words"],
                ["form", "Fill the form"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => setInput(value)}
                aria-pressed={input === value}
                className={`px-3.5 py-2 font-cond text-[0.75rem] font-semibold tracking-wide uppercase transition-colors ${
                  input === value
                    ? "bg-ink text-slip"
                    : "text-ink-45 hover:text-ink"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        ) : null}

        <main className="pb-16">
          {result ? (
            <div>
              <ResultPanel result={result} />
              <div className="mt-8">
                <button
                  type="button"
                  onClick={reset}
                  className="label border border-rule px-3 py-2 hover:border-ledger hover:text-ledger"
                >
                  ← Back
                </button>
              </div>
            </div>
          ) : scenario === "samples" ? (
            <SampleProfiles onResult={setResult} onPick={setProfile} />
          ) : input === "chat" ? (
            <ChatPanel onResult={setResult} onProfile={setProfile} />
          ) : (
            <ProfileForm onResult={setResult} onProfile={setProfile} />
          )}
        </main>

        <footer className="pb-10">
          <div className="rule-heavy flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2 pt-5">
            <StatusStrip health={health} />
            <p className="text-[0.6875rem] text-ink-45">
              A demonstration. Cards, banks and rates are fictional.
            </p>
          </div>
          <p className="mt-3 max-w-2xl text-[0.6875rem] leading-relaxed text-ink-45">
            Rules decide; the model only explains. Nothing is approved, no real
            card is offered. {profile ? "Showing a result for the profile you gave." : ""}
          </p>
        </footer>
      </div>
    </div>
  );
}
