export type RunStatus =
  "queued" | "running" | "success" | "partial_success" | "failed";
export type RunMode = "discover" | "remeasure";
export type NodeStatus =
  "pending" | "running" | "success" | "degraded" | "failed" | "skipped";
export type Surface = "chatgpt" | "google_organic" | "google_ai_overview";
export type Confidence = "high" | "medium" | "low" | "insufficient";
export type Priority = "high" | "medium" | "low";
export type ContentType =
  | "comparison_page"
  | "listicle"
  | "how_to_guide"
  | "faq_page"
  | "landing_page"
  | "case_study"
  | "glossary_definition"
  | "product_page_update"
  | "blog_post"
  | "other";
export type GeneratedBy = "llm" | "rule_based_fallback";
export interface Envelope<T> {
  data: T;
}
export interface Page<T> extends Envelope<T[]> {
  meta: { total: number; limit: number; offset: number };
}
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details: { field: string | null; message: string; type: string }[];
    request_id: string;
    meta?: Record<string, unknown>;
  };
}
export interface Market {
  location_code: number;
  language_code: string;
  location_name: string;
  language_name: string;
}
export interface Competitor {
  id: string;
  name: string;
  domain: string;
}
export interface BrandInput {
  name: string;
  domain: string;
  description: string | null;
  market: Pick<Market, "location_code" | "language_code">;
  seed_keywords: string[];
  competitors: Pick<Competitor, "name" | "domain">[];
}
export interface BrandSnapshot extends Omit<
  BrandInput,
  "market" | "competitors"
> {
  id: string;
  market: Market;
  competitors: (Omit<Competitor, "id"> & { id: string | null })[];
}
export interface Brand extends BrandSnapshot {
  competitors: Competitor[];
  created_at: string;
  updated_at: string;
  latest_run: RunSummary | null;
  stats: { run_count: number; last_run_at: string | null };
}
export interface Progress {
  percent: number;
  current_node: string | null;
  message: string;
  completed_nodes?: number;
  total_nodes?: number;
}
export interface Headline {
  queries_measured: number;
  ai_visibility_rate: number | null;
  organic_top10_rate: number | null;
  top_opportunity_query: string | null;
  top_opportunity_score: number | null;
  recommendation_count: number;
}
export interface RunSummary {
  id: string;
  brand_id: string;
  status: RunStatus;
  mode: RunMode;
  data_mode?: DataMode;
  question: string;
  progress: Progress;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  headline: Headline | null;
  error_count: number;
  status_url: string;
  report_url: string;
}
export interface RunIssue {
  node: string;
  code: string;
  message: string;
  retryable: boolean;
  provider: string | null;
  occurred_at: string;
}
export interface NodeState {
  name: string;
  label: string;
  stage:
    "setup" | "planning" | "retrieval" | "extraction" | "analysis" | "report";
  agent: string;
  status: NodeStatus;
  message: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  retry_count: number;
  error: RunIssue | null;
}
export interface Run extends RunSummary {
  correlation_id: string;
  nodes: NodeState[];
  errors: RunIssue[];
  cost: {
    provider_cost_usd: number;
    provider_calls: number;
    provider_cache_hits: number;
    llm_calls: number;
    llm_input_tokens: number;
    llm_output_tokens: number;
  };
  config: {
    force_refresh: boolean;
    max_research_queries: number;
    max_serp_calls: number;
    max_ai_visibility_calls: number;
    max_keyword_discovery_calls: number;
    llm_provider: string;
    llm_model: string;
    ai_answer_model: string;
    ai_answer_web_search: boolean;
  };
  brand_snapshot: BrandSnapshot;
}
export interface RunAccepted {
  run_id: string;
  status: RunStatus;
  status_url: string;
  report_url: string;
  run: RunSummary;
}
export interface RunInput {
  question?: string | null;
  mode: RunMode;
  force_refresh: boolean;
}
export interface KeywordMetrics {
  search_volume: number | null;
  paid_competition: number | null;
  paid_competition_level: string | null;
  cpc_usd: number | null;
  keyword_difficulty: number | null;
  search_intent: string | null;
  source: string;
  fetched_at: string;
}
export interface ResearchQuery {
  id: string;
  text: string;
  source:
    | "keyword_discovery"
    | "research_question"
    | "planner"
    | "seed_keyword"
    | "tracked_query";
  selected_for_measurement: boolean;
  ai_prompt: string | null;
  metrics: KeywordMetrics | null;
}
export interface Presence {
  mentioned: boolean | null;
  cited: boolean | null;
  rank: number | null;
  url: string | null;
}
export interface Observation {
  id: string;
  query_id: string;
  query: string;
  surface: Surface;
  status: "measured" | "not_present" | "failed" | "skipped";
  provider: string;
  endpoint: string;
  fetched_at: string;
  cached: boolean;
  cost_usd: number;
  brand: Presence;
  competitors: (Presence & { name: string; domain: string })[];
  cited_sources: { domain: string; url: string; title: string }[];
  top_results: { rank: number; domain: string; url: string; title: string }[];
  answer_excerpt: string | null;
  error: RunIssue | null;
}
export interface ScoreComponent {
  value: number | null;
  weight: number;
  measured: boolean;
  raw: Record<string, unknown>;
  explanation: string;
}
export interface Opportunity {
  id: string;
  rank: number | null;
  query_id: string;
  query: string;
  score: number | null;
  confidence: Confidence;
  coverage: number;
  is_partial: boolean;
  components: {
    demand: ScoreComponent;
    ai_gap: ScoreComponent;
    organic_gap: ScoreComponent;
    competitor_pressure: ScoreComponent;
  };
  explanation: string;
  metrics: KeywordMetrics | null;
}
export interface Recommendation {
  id: string;
  opportunity_id: string;
  query: string;
  priority: Priority;
  content_type: ContentType;
  title: string;
  angle: string;
  gap_explanation: string;
  recommended_action: string;
  supporting_observations: {
    observation_id: string;
    surface: Surface;
    summary: string;
  }[];
  limitations: string | null;
  generated_by: GeneratedBy;
}
export interface Visibility {
  queries_measured: number;
  chatgpt: {
    measured: number;
    failed: number;
    brand_mentioned: number;
    brand_cited: number;
  };
  google_organic: {
    measured: number;
    failed: number;
    brand_top10: number;
    best_rank: number | null;
  };
  google_ai_overview: {
    measured: number;
    present: number;
    brand_cited: number;
  };
  share_of_voice: {
    name: string;
    domain: string;
    is_brand: boolean;
    ai_mentions: number;
    ai_citations: number;
    organic_top10: number;
  }[];
}
export interface Scoring {
  version: string;
  weights: Record<string, number>;
  min_coverage: number;
}
export interface Report {
  run: RunSummary;
  is_final: boolean;
  brand_snapshot: BrandSnapshot;
  summary: {
    headline: string;
    key_findings: string[];
    markdown: string;
    generated_by: "deterministic";
  } | null;
  visibility: Visibility | null;
  opportunities: Opportunity[];
  recommendations: Recommendation[];
  queries: ResearchQuery[];
  observations: Observation[];
  errors: RunIssue[];
  scoring: Scoring;
  plan: {
    discovery_source: string | null;
    measurement_source: string | null;
    notes: string[];
  } | null;
  generated_at: string | null;
}
export type ComparisonStatus =
  "improved" | "declined" | "unchanged" | "new" | "dropped" | "not_comparable";
export interface ComparisonPoint {
  score: number | null;
  chatgpt_mentioned: boolean | null;
  chatgpt_cited: boolean | null;
  organic_rank: number | null;
  ai_overview_cited: boolean | null;
  search_volume: number | null;
}
export interface Comparison {
  brand_id: string;
  base_run: RunSummary;
  target_run: RunSummary;
  summary: Record<ComparisonStatus, number> & {
    ai_visibility_rate_delta: number | null;
    organic_top10_rate_delta: number | null;
    avg_score_delta: number | null;
  };
  queries: {
    query: string;
    status: ComparisonStatus;
    base: ComparisonPoint | null;
    target: ComparisonPoint | null;
    delta: { score: number | null; organic_rank: number | null };
    notes: string[];
  }[];
}
export interface PublicConfig {
  markets: Market[];
  limits: {
    max_competitors: number;
    max_seed_keywords: number;
    question_max_length: number;
    max_research_queries_per_run: number;
    max_serp_calls_per_run: number;
    max_ai_visibility_calls_per_run: number;
    max_keyword_discovery_calls_per_run: number;
    max_run_cost_usd: number;
  };
  content_types: ContentType[];
  scoring: Scoring;
  data_mode: DataMode;
  llm_quota?: LlmQuota | null;
}
export type DataMode = "live" | "sandbox";
export interface LlmQuota {
  used: number;
  limit: number;
  remaining: number;
  per_run_limit: number;
}
export interface Health {
  status: string;
  version: string;
  time: string;
  checks: {
    database: string;
    dataforseo_credentials: "configured" | "missing";
    llm_credentials: "configured" | "missing";
    llm_provider: "openai" | "anthropic" | null;
    worker: { active_runs: number | null; max_concurrent_runs: number };
  };
}
export const isActive = (status: RunStatus): boolean =>
  status === "queued" || status === "running";
