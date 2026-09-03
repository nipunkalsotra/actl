import type { ExplainResponse } from "./types";

export interface MerchantHealth {
  api: string;
  database: string;
  redis: string;
  audit_chain: string;
  payment_mode: string;
  anchor_mode: string;
}

export interface MerchantOrderItem {
  order_id: string;
  sku: string | null;
  amount_minor: number;
  currency: string;
  status: string;
  decline_reason: string | null;
  // null = organic. "demo_lab" | "growth_simulation" = a guarded
  // scenario/seeded session, never real customer activity.
  source: string | null;
  created_at: string | null;
}

export interface MerchantOrdersResponse {
  items: MerchantOrderItem[];
}

export interface MerchantCheckpoint {
  from_seq: number;
  to_seq: number;
  merkle_root: string;
  anchor_status: string;
  anchor_tx: string | null;
  anchor_chain_id: number | null;
  anchor_contract_address: string | null;
  anchored_at: string | null;
  explorer_url: string | null;
}

export interface MerchantTrustSummary {
  chain_head_seq: number | null;
  chain_head_hash: string | null;
  checkpoint_count: number;
  latest_checkpoint: MerchantCheckpoint | null;
  anchor_provider: string;
}

export interface DemoResultResponse {
  scenario: string;
  detected_fault: string | null;
  terminal_outcome: string;
  recovery_action: string;
  reserved_balance_minor: number;
  mandate_id: string;
  trace_id: string;
  seq_range: [number, number] | null;
  chain_verified: boolean | null;
  entries_verified: number | null;
}

export interface DemoVerifyChainResponse {
  ok: boolean;
  from_seq: number | null;
  to_seq: number | null;
  entries_verified: number;
  checkpoints_matched?: number[];
  head_entry_hash?: string | null;
}

// Trust Lab -- live, pollable demo runs. Every field here is either a
// direct real value (audit_seq, reason_code, amounts) or a real,
// already-persisted computed result; never a frontend-fabricated one.
export type DemoRunScenario = "stale_price" | "declined" | "llm_down" | "verify_chain";
export type DemoRunStatus = "queued" | "running" | "passed" | "failed";
export type DemoEventStatus =
  | "pending"
  | "running"
  | "passed"
  | "blocked"
  | "failed"
  | "compensated";

export interface DemoEventEvidence {
  order_id?: string;
  quote_id?: string;
  catalog_version?: number;
  gate?: string;
  reason_code?: string;
  payment_state?: string;
  reserved_balance_minor?: number;
  released_balance_minor?: number;
  audit_seq?: number;
  entry_hash_prefix?: string;
  checkpoint_status?: string;
}

export interface DemoEvent {
  seq: number;
  ts: string;
  phase: string;
  kind: string;
  title: string;
  detail: string;
  status: DemoEventStatus;
  evidence: DemoEventEvidence;
}

export interface DemoRunResult {
  scenario: string;
  detected_fault: string | null;
  terminal_outcome: string;
  recovery_action: string;
  reserved_balance_minor: number;
  mandate_id: string | null;
  trace_id: string | null;
  order_id: string | null;
  seq_range: [number, number] | null;
  chain_verified: boolean | null;
  entries_verified: number | null;
}

export interface DemoRun {
  run_id: string;
  scenario: string;
  status: DemoRunStatus;
  started_at: string;
  completed_at: string | null;
  events: DemoEvent[];
  result: DemoRunResult | null;
  order_id: string | null;
  error: string | null;
}

export interface GrowthArmMetrics {
  arm: string;
  sessions: number;
  orders: number;
  conversion_rate: number;
  aov_minor: number;
  upsell_offered: number;
  upsell_accepted: number;
  attach_rate: number;
}

export interface MerchantOrderAudit extends ExplainResponse {
  chain_verified: boolean | null;
}

// §28 P12: the real, buyer-driven upsell counters -- deliberately
// separate from `baseline`/`upsell` above (the synthetic growth-simulator
// A/B arms). `attach_rate` is null when `offered` is 0 (no invented rate).
export interface RealUpsellMetrics {
  offered: number;
  accepted: number;
  settled: number;
  declined: number;
  attach_rate: number | null;
  settled_revenue_minor: number;
}

export interface OrganicSalesMetrics {
  orders: number;
  gross_sales_minor: number;
}

export interface MerchantKpisResponse {
  baseline: GrowthArmMetrics;
  upsell: GrowthArmMetrics;
  revenue_uplift: number;
  protected_offers_blocked: number;
  real_upsell: RealUpsellMetrics;
  organic: OrganicSalesMetrics;
}
