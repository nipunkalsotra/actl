// Typed mirror of `interfaces/http/routers/buyer.py` (and the reused
// plain-REST `/agent/v1/*` routes) response shapes. Every field here
// exists on the real backend response -- nothing is invented client-side.

export interface CatalogLocation {
  city: string;
  country: string;
}

export interface CatalogAttributes {
  rating: number;
  sea_facing: boolean;
  breakfast_included: boolean;
}

export interface CatalogPolicy {
  refundable: boolean;
  cancellation_window_h: number;
  instant_confirm: boolean;
  taxes_included: boolean;
}

export interface CatalogItem {
  sku: string;
  category: string;
  merchant_id: string;
  unit: string;
  unit_price_minor: number;
  available_units: number;
  location: CatalogLocation;
  attributes: CatalogAttributes;
  policy: CatalogPolicy;
  version: number;
  quote_required: boolean;
}

export interface CatalogResponse {
  catalog_version: number;
  generated_at: string;
  currency: string;
  ranked: boolean;
  degraded: boolean | null;
  items: CatalogItem[];
}

export interface ClarificationNeeded {
  status: "clarification_needed";
  missing_slots: string[];
  questions: string[];
}

export interface MandateDraftReady {
  status: "draft_ready";
  max_total_minor: number;
  max_unit_minor: number | null;
  slots: {
    category: string | null;
    location: string | null;
    check_in: string | null;
    nights: number | null;
    rooms: number | null;
    currency: string | null;
  };
}

export type ExtractResponse = ClarificationNeeded | MandateDraftReady;

export interface MandateBounds {
  currency: string;
  max_total_minor: number;
  max_unit_minor: number;
  max_transactions: number;
  allowed_categories: string[];
  blocked_merchants: string[];
  require_refundable: boolean;
  max_price_delta_bps: number;
}

export interface MandateIntent {
  category: string;
  location: string;
  check_in: string;
  nights: number;
  rooms: number;
}

export interface MandateResponse {
  mandate_id: string;
  spec_hash: string;
  status: "LOCKED";
  intent: MandateIntent;
  bounds: MandateBounds;
  expires_at: string;
}

export interface QuoteResponse {
  schema: string;
  quote_id: string;
  sku: string;
  mandate_id: string;
  unit_price_minor: number;
  nights: number;
  total_minor: number;
  currency: string;
  catalog_version: number;
  refundable: boolean;
  expires_at: string;
  quote_token: string;
  quote_hash: string;
}

export interface ProposeAccepted {
  decision: "accept";
  order_id: string;
  saga_id: string;
}

export interface ProposeRejected {
  decision: "reject";
  reason_code: string;
  trace_id: string;
}

export type ProposeResponse = ProposeAccepted | ProposeRejected;

export interface CheckoutResponse {
  saga_id: string;
  mandate_id: string;
  status: string;
  step: string;
  order_id: string | null;
  reason_code: string | null;
}

export interface OrderStatusResponse {
  order_id: string;
  status: string;
  amount_minor: number;
  currency: string;
  provider_payment_id: string | null;
  audit_seq_from: number | null;
  audit_seq_to: number | null;
}

export interface AnchorInfo {
  status: string;
  checkpoint_range: { from_seq: number; to_seq: number };
  chain_id: number | null;
  contract_address: string | null;
  tx_hash: string | null;
  explorer_url: string | null;
}

export interface TimelineItem {
  seq: number | null;
  ts: string | null;
  type: "fact" | "decision" | "provider_event" | "compensation";
  action: string;
  trace_id: string | null;
  hashes: { entry_hash: string | null; prev_hash: string | null; payload_hash: string | null };
  payload: Record<string, unknown>;
}

export interface ExplainResponse {
  order_id: string;
  terminal_outcome: { status: string };
  anchor: AnchorInfo | null;
  timeline: TimelineItem[];
}

export interface ConfigResponse {
  currency: string;
  location: string;
  payment_provider: string;
  razorpay_key_id: string | null;
  quote_ttl_s: number;
}
