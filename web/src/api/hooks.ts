import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiPost } from "./client";
import type {
  CatalogResponse,
  CheckoutResponse,
  ConfigResponse,
  ExtractResponse,
  MandateResponse,
  OrderStatusResponse,
  ProposeResponse,
  QuoteResponse,
  UpsellOffersResponse,
  UpsellPurchaseResponse,
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
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { order_id: string; saga_id: string }) =>
      apiPost<CheckoutResponse>("/buyer/v1/checkout", input),
    onSuccess: () => {
      // A real settlement (captured or declined/compensated) changes real
      // Merchant-visible data (Live Orders, KPIs, audit/trust counters) --
      // this SPA shares one QueryClient across the Buyer and Merchant
      // routes, so this invalidation is enough for "already on /merchant
      // in this tab"; useMerchantOrders/useMerchantKpis/useMerchantTrust's
      // own polling covers a separate tab/window.
      void queryClient.invalidateQueries({ queryKey: ["merchant", "orders"] });
      void queryClient.invalidateQueries({ queryKey: ["merchant", "kpis"] });
      void queryClient.invalidateQueries({ queryKey: ["merchant", "trust"] });
    },
  });
}

export function useOrderStatus(orderId: string | null) {
  return useQuery({
    queryKey: ["order", orderId],
    queryFn: () => apiGet<OrderStatusResponse>(`/buyer/v1/order/${orderId}`),
    enabled: orderId !== null,
  });
}

// §28 P12 contextual upsell -- enabled only once the caller knows the
// base order actually reached its real terminal CAPTURED state; the
// backend re-derives eligibility from real data regardless, this just
// avoids a pointless call before that's possible.
export function useUpsellOffers(baseOrderId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ["upsell-offers", baseOrderId],
    queryFn: () => apiGet<UpsellOffersResponse>(`/buyer/v1/upsell/offers?order_id=${baseOrderId}`),
    enabled: enabled && baseOrderId !== null,
  });
}

export function usePurchaseUpsell() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { base_order_id: string; offer_sku: string }) =>
      apiPost<UpsellPurchaseResponse>("/buyer/v1/upsell/purchase", input),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ["upsell-offers", variables.base_order_id] });
      // Attach rate / upsell revenue / add-on order data on the Merchant
      // dashboard are all real-time derived from this same purchase.
      void queryClient.invalidateQueries({ queryKey: ["merchant", "orders"] });
      void queryClient.invalidateQueries({ queryKey: ["merchant", "kpis"] });
    },
  });
}

export function useDeclineUpsell() {
  return useMutation({
    mutationFn: (input: { base_order_id: string }) =>
      apiPost<{ status: string }>("/buyer/v1/upsell/decline", input),
  });
}
