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

export interface MerchantKpisResponse {
  baseline: GrowthArmMetrics;
  upsell: GrowthArmMetrics;
  revenue_uplift: number;
  protected_offers_blocked: number;
}
