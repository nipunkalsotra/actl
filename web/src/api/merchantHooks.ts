import { useMutation, useQuery, useQueryClient, type Query } from "@tanstack/react-query";
import { apiGet, apiPost } from "./client";
import type {
  DemoResultResponse,
  DemoRun,
  DemoRunScenario,
  DemoVerifyChainResponse,
  MerchantHealth,
  MerchantKpisResponse,
  MerchantOrderAudit,
  MerchantOrdersResponse,
  MerchantTrustSummary,
} from "./merchantTypes";

export function useMerchantHealth() {
  return useQuery({
    queryKey: ["merchant", "health"],
    queryFn: () => apiGet<MerchantHealth>("/merchant/v1/health"),
    refetchInterval: 30_000,
  });
}

// A sensible poll while the Merchant page is open: a real Buyer booking or
// upsell happens on a page this SPA may not have open at all (another tab,
// or the Buyer route in this same tab) -- useCheckout/usePurchaseUpsell's
// own invalidation covers the same-tab/same-session case, this covers
// everything else without needing WebSockets/SSE for a buildathon demo.
const MERCHANT_LIVE_POLL_INTERVAL_MS = 15_000;

export type MerchantOrderScope = "organic" | "demo" | "all";

export function useMerchantOrders(limit = 50, scope: MerchantOrderScope = "organic") {
  return useQuery({
    queryKey: ["merchant", "orders", limit, scope],
    queryFn: () =>
      apiGet<MerchantOrdersResponse>(`/merchant/v1/orders?limit=${limit}&scope=${scope}`),
    refetchInterval: MERCHANT_LIVE_POLL_INTERVAL_MS,
  });
}

export function useMerchantOrderAudit(orderId: string | null) {
  return useQuery({
    queryKey: ["merchant", "order-audit", orderId],
    queryFn: () => apiGet<MerchantOrderAudit>(`/merchant/v1/order/${orderId}/audit`),
    enabled: orderId !== null,
  });
}

export function useMerchantTrust() {
  return useQuery({
    queryKey: ["merchant", "trust"],
    queryFn: () => apiGet<MerchantTrustSummary>("/merchant/v1/trust"),
    refetchInterval: MERCHANT_LIVE_POLL_INTERVAL_MS,
  });
}

export function useMerchantKpis() {
  return useQuery({
    queryKey: ["merchant", "kpis"],
    queryFn: () => apiGet<MerchantKpisResponse>("/merchant/v1/kpis"),
    refetchInterval: MERCHANT_LIVE_POLL_INTERVAL_MS,
  });
}

export type DemoScenarioEndpoint = "stale-price" | "payment-decline" | "llm-unavailable";

export function useRunDemoScenario() {
  return useMutation({
    mutationFn: (endpoint: DemoScenarioEndpoint) =>
      apiPost<DemoResultResponse>(`/merchant/v1/demo/${endpoint}`, {}),
  });
}

export function useRunVerifyChainDemo() {
  return useMutation({
    mutationFn: () => apiPost<DemoVerifyChainResponse>("/merchant/v1/demo/verify-chain", {}),
  });
}

// ---------------------------------------------------------------------------
// Trust Lab -- live, pollable demo runs.
// ---------------------------------------------------------------------------

export function useStartDemoRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (scenario: DemoRunScenario) =>
      apiPost<DemoRun>("/merchant/v1/demo-runs", { scenario }),
    onSuccess: () => {
      // A run can create a real order and move real ledger/audit state --
      // Live Orders/KPIs/Trust should reflect that the next time a judge
      // looks, not just after an unrelated manual refresh.
      void queryClient.invalidateQueries({ queryKey: ["merchant", "orders"] });
      void queryClient.invalidateQueries({ queryKey: ["merchant", "kpis"] });
      void queryClient.invalidateQueries({ queryKey: ["merchant", "trust"] });
    },
  });
}

const DEMO_RUN_POLL_INTERVAL_MS = 300;

export function useDemoRun(runId: string | null) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: ["merchant", "demo-run", runId],
    queryFn: () => apiGet<DemoRun>(`/merchant/v1/demo-runs/${runId}`),
    enabled: runId !== null,
    refetchInterval: (query: Query<DemoRun>) => {
      const status = query.state.data?.status;
      if (status === "passed" || status === "failed") {
        // Terminal -- one more invalidation in case events arrived after
        // the mutation's own onSuccess already fired.
        void queryClient.invalidateQueries({ queryKey: ["merchant", "orders"] });
        void queryClient.invalidateQueries({ queryKey: ["merchant", "kpis"] });
        void queryClient.invalidateQueries({ queryKey: ["merchant", "trust"] });
        return false;
      }
      return DEMO_RUN_POLL_INTERVAL_MS;
    },
  });
}
