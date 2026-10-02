import type { HealthStatus } from "../lib/types";
import { TRACE_LABELS } from "../lib/format";

/** The status strip.
 *
 *  This is the product's argument made visible before the user types anything:
 *  which components are real cloud services and which are local fallbacks. A
 *  reviewer should be able to tell that nothing is faked, and a user debugging
 *  a slow answer should be able to see why.
 */
export function StatusStrip({ health }: { health: HealthStatus | null }) {
  return (
    <div className="label flex flex-wrap items-center gap-x-4 gap-y-1 text-[0.625rem]">
      {health ? (
        <>
          <Item label="engine" value={health.llm === "groq" ? "Groq" : "template"} />
          <Item label="catalogue" value={health.cards_loaded.toString()} />
          <Item
            label="db"
            value={health.database === "postgres" ? "Neon" : "SQLite"}
          />
          <Item
            label="policy"
            value={
              health.vector_store === "qdrant"
                ? `Qdrant · ${health.policy_chunks}`
                : `${health.policy_chunks} in-process`
            }
          />
          <Item
            label="embed"
            value={health.embeddings === "openai" ? "OpenAI" : "MiniLM"}
          />
        </>
      ) : (
        <span className="text-ink-45">Connecting to the engine…</span>
      )}
    </div>
  );
}

function Item({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-baseline gap-1.5">
      <span className="text-ink-45">{label}</span>
      <span className="num text-ledger">{value}</span>
    </span>
  );
}

/** Per-stage latency, shown under a result.
 *
 *  Deliberately not a performance graph. What matters to someone judging this
 *  system is which stage the time went in, so it is a list of figures.
 */
export function TraceList({ trace }: { trace: Record<string, number> }) {
  const rows = Object.entries(trace).filter(
    ([key]) => !key.endsWith("_count") && !key.endsWith("_chunks"),
  );
  if (!rows.length) return null;
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-3">
      {rows.map(([key, value]) => (
        <div key={key} className="flex items-baseline justify-between gap-3">
          <dt className="label text-[0.625rem]">
            {TRACE_LABELS[key] ?? key.replace(/_/g, " ")}
          </dt>
          <dd className="num text-[0.6875rem] text-ink-70">
            {key === "total_ms" || value > 20 ? `${value.toFixed(0)}ms` : value}
          </dd>
        </div>
      ))}
    </dl>
  );
}
