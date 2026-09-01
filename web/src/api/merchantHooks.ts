import { useMutation, useQuery } from "@tanstack/react-query";
import { apiGet, apiPost } from "./client";
import type {
  DemoResultResponse,
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

export function useMerchantOrders(limit = 50) {
  return useQuery({
    queryKey: ["merchant", "orders", limit],
    queryFn: () => apiGet<MerchantOrdersResponse>(`/merchant/v1/orders?limit=${limit}`),
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
  });
}

export function useMerchantKpis() {
  return useQuery({
    queryKey: ["merchant", "kpis"],
    queryFn: () => apiGet<MerchantKpisResponse>("/merchant/v1/kpis"),
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
