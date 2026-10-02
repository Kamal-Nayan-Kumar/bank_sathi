import type {
  ChatReply,
  ExampleBucket,
  HealthStatus,
  ProfileFields,
  RecommendationResponse,
  UserProfile,
} from "./types";

/** Base URL for the API.
 *
 *  VITE_API_URL  set on Vercel to the Render backend origin.
 *  otherwise     same-origin, which works in `vite dev` via the proxy and in a
 *                production build served behind the same domain.
 */
const BASE = (import.meta.env.VITE_API_URL ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    // A network failure has no status, so it is phrased as reachability rather
    // than as a rejection. Telling someone "404" when the fetch never left is
    // the kind of vague error this product is arguing against.
    throw new ApiError(
      `Could not reach the engine at ${BASE || "this origin"}. It may be starting up or asleep.`,
      0,
    );
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
      else if (Array.isArray(body?.detail) && body.detail[0]?.msg) {
        detail = body.detail[0].msg;
      }
    } catch {
      /* non-JSON error body: the status line is all we have */
    }
    throw new ApiError(detail, response.status);
  }

  return (await response.json()) as T;
}

export const api = {
  chat: (message: string, sessionId: string) =>
    request<ChatReply>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message, session_id: sessionId }),
    }),

  recommend: (profile: UserProfile) =>
    request<RecommendationResponse>("/api/recommend", {
      method: "POST",
      body: JSON.stringify({ profile }),
    }),

  followup: (message: string, profile: UserProfile | null, history: ChatTurn[]) =>
    request<{ reply: string }>("/api/followup", {
      method: "POST",
      body: JSON.stringify({ message, profile, history }),
    }),

  fields: () => request<ProfileFields>("/api/profile/fields"),
  examples: () => request<{ buckets: ExampleBucket; note: string }>("/api/examples"),
  health: () => request<HealthStatus>("/api/health"),
};

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}
