import { useEffect, useRef, useState } from "react";
import { api, ApiError, type ChatTurn } from "../lib/api";
import type { RecommendationResponse, UserProfile } from "../lib/types";
import { fieldLabel, rupees, totalSpend } from "../lib/format";

interface Message {
  id: number;
  role: "user" | "assistant";
  content: string;
  /** Attached to assistant turns so the transcript stays honest about where
   *  an answer came from. */
  source?: string;
}

/** The chat route.
 *
 *  The session's memory lives in the client, not in a server-side session
 *  table: the API stays stateless and horizontally scalable, and the sidebar
 *  can show exactly what the engine currently believes about the customer,
 *  which is the more useful thing to display anyway.
 */
export function ChatPanel({
  onResult,
  onProfile,
}: {
  onResult: (result: RecommendationResponse) => void;
  onProfile: (profile: UserProfile | null) => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [partial, setPartial] = useState<Record<string, unknown>>({});
  const [missing, setMissing] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const nextId = useRef(1);
  const logRef = useRef<HTMLDivElement>(null);
  const stageTimer = useRef<number | null>(null);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  /** While the request runs, narrate the real stages in order.
   *
   *  These are the pipeline's actual steps, shown as they would run. Timed to
   *  typical durations, so on a slow request they lag behind reality rather
   *  than racing ahead — the completed panel afterwards carries the true
   *  timings for every step.
   */
  const STAGES = [
    "Reading that…",
    "Pulling your details together…",
    "Checking 120 cards against your profile…",
    "Ranking what fits…",
    "Writing it up…",
  ];

  function startStages(setter: (updater: (m: Message[]) => Message[]) => void) {
    let i = 0;
    const id = nextId.current++;
    setter((m) => [...m, { id, role: "assistant", content: STAGES[0], source: "working" }]);
    stageTimer.current = window.setInterval(() => {
      i += 1;
      if (i >= STAGES.length) {
        if (stageTimer.current) window.clearInterval(stageTimer.current);
        return;
      }
      const text = STAGES[i];
      setter((m) => m.map((msg) => (msg.id === id ? { ...msg, content: text } : msg)));
    }, 5000);
  }

  function stopStages() {
    if (stageTimer.current) {
      window.clearInterval(stageTimer.current);
      stageTimer.current = null;
    }
  }

  /** Put a prompt in the box and send it.
   *
   *  Example prompts used to only focus the input, which looked like a broken
   *  button. A demo that needs two clicks to try itself is a demo nobody
   *  tries, so this fills and submits.
   */
  async function send(text: string) {
    const message = text.trim();
    if (!message || busy) return;

    const turn: Message = { id: nextId.current++, role: "user", content: message };
    setMessages((m) => [...m, turn]);
    setDraft("");
    setError(null);
    setBusy(true);
    startStages(setMessages);

    try {
      // partial is read at call time, so each turn carries everything known so
      // far and the profile only ever fills up.
      const reply = await api.chat(message, "web-session", partial);
      stopStages();
      // The staged "working" line has served its purpose; the real answer and
      // the step-by-step panel replace it.
      setMessages((m) => m.filter((msg) => msg.source !== "working"));
      setPartial(reply.partial);
      setMissing(reply.missing_fields);
      setProfile(reply.response.profile);
      onProfile(reply.response.profile);

      // A pending turn is part of the conversation, not a result. Handing it to
      // the results panel unmounted this chat and threw the transcript away, so
      // asking one question felt like the app gave up. Only a settled answer
      // moves on.
      const pending = reply.response.status === "need_more_information";
      if (!pending) onResult(reply.response);

      const answer = reply.response.question ?? reply.response.summary;
      if (answer) {
        setMessages((m) => [
          ...m,
          {
            id: nextId.current++,
            role: "assistant",
            content: answer,
            source: pending
              ? "one more thing"
              : reply.response.verifier?.used_fallback
                ? "written from the engine's figures"
                : undefined,
          },
        ]);
      }
    } catch (err) {
      stopStages();
      setMessages((m) => m.filter((msg) => msg.source !== "working"));
      const message = err instanceof ApiError ? err.message : "Something went wrong.";
      setError(message);
      setMessages((m) => [
        ...m,
        { id: nextId.current++, role: "assistant", content: message, source: "error" },
      ]);
    } finally {
      setBusy(false);
    }
  }

  async function ask(question: string) {
    if (busy) return;
    setMessages((m) => [
      ...m,
      { id: nextId.current++, role: "user", content: question },
    ]);
    setBusy(true);
    setError(null);
    try {
      const history: ChatTurn[] = messages
        .filter((m) => !m.source || m.source !== "error")
        .map((m) => ({ role: m.role, content: m.content }));
      const { reply } = await api.followup(question, profile, history);
      setMessages((m) => [
        ...m,
        { id: nextId.current++, role: "assistant", content: reply },
      ]);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  const settled = partial && Object.keys(partial).length > 0;

  /** The box should invite the answer, not repeat the question. */
  const placeholder = missing.length
    ? `Tell me about your ${fieldLabel(missing[0])}…`
    : "Describe yourself in your own words…";

  return (
    <div>
      <div
        ref={logRef}
        className="min-h-[12rem] space-y-4 border border-rule bg-slip/60 px-4 py-5"
      >
          {messages.length === 0 ? (
            <Opening />
          ) : (
            messages.map((m) => (
              <div key={m.id} className={m.role === "user" ? "text-right" : ""}>
                <p
                  className={`inline-block max-w-[85%] text-[0.9375rem] leading-relaxed ${
                    m.role === "user"
                      ? "bg-ledger px-3.5 py-2 text-slip"
                      : "border-l-2 border-ledger bg-paper/70 px-3.5 py-2 text-ink"
                  }`}
                >
                  {m.content}
                </p>
                {m.source ? (
                  <p className="label mt-1 text-[0.5625rem]">{m.source}</p>
                ) : null}
              </div>
            ))
          )}
          {busy ? (
            <p className="label flex items-center gap-2">
              <span className="inline-block size-1.5 animate-pulse bg-ledger" />
              {settled ? "Checking your profile against the catalogue" : "Reading that"}
            </p>
          ) : null}
        </div>

        <ProfileChips partial={partial} missing={missing} profile={profile} />

        {messages.length > 0 && profile ? (
          <div className="mt-3 flex flex-wrap gap-2">
            {["Why not the second one?", "Compare my top two", "What is the lounge policy?"].map(
              (q) => (
                <button
                  key={q}
                  type="button"
                  onClick={() => ask(q)}
                  disabled={busy}
                  className="label border border-rule px-2.5 py-1.5 text-ink-70 hover:border-ledger hover:text-ledger disabled:opacity-40"
                >
                  {q}
                </button>
              ),
            )}
          </div>
        ) : null}

        <form
          className="mt-4 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void send(draft);
          }}
        >
          <label htmlFor="chat-input" className="sr-only">
            Describe yourself in your own words
          </label>
          <input
            id="chat-input"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={placeholder}
            autoComplete="off"
            disabled={busy}
            className="flex-1 border border-rule bg-slip px-3.5 py-3 text-[0.9375rem] text-ink placeholder:text-ink-45 focus:border-ledger focus:outline-none disabled:opacity-60"
          />
          <button
            type="submit"
            disabled={busy || !draft.trim()}
            className="bg-ledger px-5 py-3 font-cond text-sm font-semibold tracking-wide text-slip uppercase hover:bg-ink disabled:opacity-40"
          >
            Check
          </button>
        </form>

        {error ? (
          <p role="alert" className="mt-3 border-l-2 border-stamp bg-stamp-10 px-3 py-2 text-[0.8125rem] text-ink">
            {error}
          </p>
        ) : null}

        <p className="mt-3 text-[0.6875rem] leading-relaxed text-ink-45">
          Don&apos;t include your PAN, Aadhaar number or card details — anything
          that looks like one is stripped before your message reaches the model.
        </p>
    </div>
  );
}

/** What the engine has understood so far, as a single line of chips.
 *
 *  This replaces the sidebar: the same information, but it reads in one glance
 *  and disappears entirely when there is nothing to show.
 */
function ProfileChips({
  partial,
  missing,
  profile,
}: {
  partial: Record<string, unknown>;
  missing: string[];
  profile: UserProfile | null;
}) {
  const known = Object.entries(partial).filter(
    ([k, v]) => k !== "monthly_spend" && k !== "preferences" && v !== null,
  );
  if (!known.length && !missing.length) return null;
  return (
    <div
      aria-live="polite"
      className="mt-3 flex flex-wrap items-center gap-1.5"
    >
      {known.map(([k, v]) => (
        <span
          key={k}
          className="num border border-ledger/25 bg-ledger-10/60 px-2 py-0.5 text-[0.6875rem] text-ledger"
        >
          {fieldLabel(k)} {String(v)}
        </span>
      ))}
      {profile ? (
        <span className="num border border-ledger/25 bg-ledger-10/60 px-2 py-0.5 text-[0.6875rem] text-ledger">
          spends {rupees(totalSpend(profile.monthly_spend))}/mo
        </span>
      ) : null}
      {missing.map((f) => (
        <span
          key={f}
          className="border border-dashed border-ochre/50 px-2 py-0.5 font-cond text-[0.6875rem] font-semibold tracking-wide text-ochre uppercase"
        >
          need: {fieldLabel(f)}
        </span>
      ))}
    </div>
  );
}

function Opening() {
  return (
    <div className="py-2">
      <p className="max-w-prose text-[0.9375rem] leading-relaxed text-ink-70">
        Tell me about yourself however you like — income, city, what you spend
        on. I&apos;ll work out which cards you qualify for and show my working.
      </p>
    </div>
  );
}
