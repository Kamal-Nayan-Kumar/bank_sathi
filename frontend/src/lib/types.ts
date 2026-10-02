/** Shapes returned by the backend. Kept hand-written and narrow: the frontend
 *  reads only what it renders, so an extra backend field cannot quietly become
 *  an implicit dependency. */

export type SpendCategory =
  | "fuel"
  | "dining"
  | "groceries"
  | "online_shopping"
  | "travel"
  | "utilities"
  | "other";

export type SpendMix = Record<SpendCategory, number>;

export interface UserProfile {
  profile_id: string;
  age: number;
  monthly_income: number;
  employment: string;
  city_tier: 1 | 2 | 3;
  cibil_score: number | null;
  existing_cards: number;
  missed_payments_12m: number;
  recent_inquiries_6m: number;
  utilization_pct: number | null;
  monthly_spend: SpendMix;
  preferences: string[];
}

export interface Reason {
  code: string;
  label: string;
  message: string;
  actual: number | string | null;
  required: number | string | null;
  gap: number | null;
  fixable: boolean;
  near_miss: boolean;
}

export interface CardEvaluation {
  card_id: string;
  eligible: boolean;
  reasons: Reason[];
}

export interface Recommendation {
  rank: number;
  card_id: string;
  name: string;
  bank: string;
  tier: string;
  score: number;
  net_annual_value_rs: number;
  est_credit_limit_rs: number;
  apr_pct: number;
  fee_payable_rs: number;
  key_benefits: string[];
  why: string;
  sources: string[];
}

export interface ScoreBreakdown {
  card_id: string;
  net_value: number;
  spend_alignment: number;
  preference_match: number;
  fee_fit: number;
  total: number;
}

export interface PolicyEvidence {
  source: string;
  card_id: string | null;
  section: string;
  text: string;
  score: number;
}

export interface VerifierCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface VerifierReport {
  passed: boolean;
  checks: VerifierCheck[];
  notes: string[];
  retries: number;
  used_fallback: boolean;
}

export interface RecommendationResponse {
  profile_id: string;
  status:
    | "recommendations_available"
    | "no_matching_cards"
    | "need_more_information"
    | "profile_rejected";
  decision: "recommended" | "rejected" | "pending";
  profile: UserProfile | null;
  recommendations: Recommendation[];
  rejections: CardEvaluation[];
  rejected_cards: Reason[];
  near_miss: CardEvaluation[];
  improvement_steps: string[];
  missing_fields: string[];
  question: string | null;
  summary: string;
  evidence: PolicyEvidence[];
  score_breakdown: ScoreBreakdown[];
  verifier: VerifierReport | null;
  trace: Record<string, number>;
}

export interface ChatReply {
  response: RecommendationResponse;
  session_id: string;
  partial: Record<string, unknown>;
  missing_fields: string[];
}

export interface FieldSpec {
  name: string;
  type: "number" | "select" | "multiselect" | "spend" | "text" | "checkbox";
  required: boolean;
  minimum: number | null;
  maximum: number | null;
  options: string[] | null;
  help: string;
}

export interface ProfileFields {
  fields: FieldSpec[];
  spend_categories: SpendCategory[];
  preferences: string[];
}

export interface ExampleBucket {
  recommended: Example[];
  borderline: Example[];
  rejected: Example[];
  edge_case: Example[];
}

export interface Example {
  profile_id: string;
  profile: UserProfile;
}

export interface HealthStatus {
  database: string;
  vector_store: string;
  embeddings: string;
  llm: string;
  cards_loaded: number;
  policy_chunks: number;
  database_error?: string;
  vector_store_error?: string;
  embeddings_error?: string;
}
