import { useEffect, useState } from "react";
import { api } from "./lib/api";
import type { HealthStatus, RecommendationResponse, UserProfile } from "./lib/types";
import { StatusStrip } from "./components/StatusStrip";
import { ChatPanel } from "./components/ChatPanel";
import { ProfileForm } from "./components/ProfileForm";
import { ResultPanel } from "./components/ResultPanel";

type Mode = "chat" | "form";

export default function App() {
  const [mode, setMode] = useState<Mode>("chat");
  const [result, setResult] = useState<RecommendationResponse | null>(null);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [profile, setProfile] = useState<UserProfile | null>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => undefined);
  }, []);

  return (
    <div className="min-h-dvh">
      <Masthead mode={mode} setMode={setMode} />

      <main className="mx-auto w-full max-w-5xl px-5 pb-20 sm:px-8">
        {result ? (
          <div className="mb-10">
            <ResultPanel result={result} />
            <div className="mt-8 flex items-center gap-4">
              <button
                type="button"
                onClick={() => setResult(null)}
                className="label border border-rule px-3 py-2 hover:border-ledger hover:text-ledger"
              >
                Start over
              </button>
            </div>
            <FooterNote profile={profile} />
          </div>
        ) : mode === "chat" ? (
          <ChatPanel onResult={setResult} onProfile={setProfile} />
        ) : (
          <ProfileForm onResult={setResult} onProfile={setProfile} />
        )}
      </main>

      <footer className="rule-heavy mx-auto w-full max-w-5xl px-5 pb-10 sm:px-8">
        <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2 pt-5">
          <StatusStrip health={health} />
          <p className="text-[0.6875rem] text-ink-45">
            Bank Sathi is a demonstration. Cards, banks and rates are fictional.
          </p>
        </div>
      </footer>
    </div>
  );
}

function Masthead({ mode, setMode }: { mode: Mode; setMode: (m: Mode) => void }) {
  return (
    <header className="mx-auto w-full max-w-6xl px-5 pt-12 pb-8 sm:px-8">
      <div className="grid gap-x-10 gap-y-8 lg:grid-cols-[1fr_auto] lg:items-end">
        {/* Wordmark. "Sathi" means companion — a ledger's companion, which is
            the point of the product. The mark is set in the ledger green of a
            printed account block, with the tagline hung off it as a subtitle
            rather than floated beneath. */}
        <div>
          <div className="flex items-baseline gap-4">
            <h1 className="font-cond text-5xl leading-none font-bold tracking-tight text-ink sm:text-6xl">
              Bank <span className="text-ledger">Sathi</span>
            </h1>
            <span className="hidden font-cond text-[0.6875rem] font-semibold tracking-[0.18em] text-ink-45 uppercase sm:inline">
              Credit card desk
            </span>
          </div>
          <p className="mt-4 max-w-xl text-[1.0625rem] leading-relaxed text-ink-70">
            Credit cards, matched to how you actually spend — with every figure
            traced to the rule that produced it.
          </p>
        </div>

        <nav aria-label="How to start" className="flex w-max border border-rule bg-slip">
          {(
            [
              ["chat", "Describe yourself"],
              ["form", "Fill in details"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setMode(value)}
              aria-current={mode === value}
              className={`px-4 py-2.5 font-cond text-[0.8125rem] font-semibold tracking-wide uppercase transition-colors ${
                mode === value
                  ? "bg-ledger text-slip"
                  : "text-ink-70 hover:bg-paper hover:text-ink"
              }`}
            >
              {label}
            </button>
          ))}
        </nav>
      </div>

      {/* The claim the page then has to honour. Stated once, up front, and
          kept in view rather than buried in a footer — it is the reason to
          trust anything below it. */}
      <p className="rule-heavy mt-9 max-w-2xl border-l-2 border-ledger py-3 pl-4 text-[0.875rem] leading-relaxed text-ink-70">
        Eligibility, ranking and every number are decided in code, not by a
        model. The model reads what you write and explains the result. Nothing
        here is approved, and no real card is offered.
      </p>
    </header>
  );
}

function FooterNote({ profile }: { profile: unknown }) {
  if (!profile) return null;
  return (
    <p className="mt-6 text-[0.75rem] text-ink-45">
      Based on the profile you gave. Change anything above and check again — the
      answer changes only where a rule says it should.
    </p>
  );
}
