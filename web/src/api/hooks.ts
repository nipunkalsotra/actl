import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiPost } from "./client";
import type {
  CatalogResponse,
  CheckoutResponse,
  ConfigResponse,
  ExplainResponse,
  ExtractResponse,
  MandateResponse,
  OrderStatusResponse,
  ProposeResponse,
  QuoteResponse,
} from "./types";

export function useConfig() {
  return useQuery({
    queryKey: ["config"],
    queryFn: () => apiGet<ConfigResponse>("/buyer/v1/config"),
    staleTime: Infinity,
  });
}

export function useCatalog(mandateId: string | null) {
  return useQuery({
    queryKey: ["catalog", mandateId],
    queryFn: () =>
      apiGet<CatalogResponse>(
        mandateId ? `/buyer/v1/catalog?mandate_id=${mandateId}` : "/buyer/v1/catalog",
      ),
  });
}

export function useExtractMandate() {
  return useMutation({
    mutationFn: (conversationText: string) =>
      apiPost<ExtractResponse>("/buyer/v1/mandate/extract", {
        conversation_text: conversationText,
      }),
  });
}

export interface CreateMandateInput {
  nights: number;
  rooms: number;
  max_total_minor: number;
  require_refundable: boolean;
  check_in: string;
}

export function useCreateMandate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateMandateInput) => apiPost<MandateResponse>("/buyer/v1/mandate", input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["catalog"] });
    },
  });
}

export function useCreateQuote() {
  return useMutation({
    mutationFn: (input: { sku: string; mandate_id: string; nights: number }) =>
      apiPost<QuoteResponse>("/agent/v1/quote", input),
  });
}

export function useProposeOrder() {
  return useMutation({
    mutationFn: (input: { quote_id: string; mandate_id: string }) =>
      apiPost<ProposeResponse>("/buyer/v1/order/propose", input),
  });
}

export function useCheckout() {
  return useMutation({
    mutationFn: (input: { order_id: string; saga_id: string }) =>
      apiPost<CheckoutResponse>("/buyer/v1/checkout", input),
  });
}

export function useOrderStatus(orderId: string | null) {
  return useQuery({
    queryKey: ["order", orderId],
    queryFn: () => apiGet<OrderStatusResponse>(`/buyer/v1/order/${orderId}`),
    enabled: orderId !== null,
  });
}

export function useAuditExplain(orderId: string | null) {
  return useQuery({
    queryKey: ["explain", orderId],
    queryFn: () => apiGet<ExplainResponse>(`/buyer/v1/audit/explain/${orderId}`),
    enabled: orderId !== null,
  });
}
