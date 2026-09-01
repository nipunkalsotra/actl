import { useCheckout, useProposeOrder } from "../api/hooks";
import { useJourney } from "./journeyContext";

export interface PurchaseParams {
  quoteId: string;
  mandateId: string;
  sku: string;
  hotelName: string;
  totalMinor: number;
}

export interface PurchaseResult {
  ok: boolean;
  orderId: string | null;
  reasonCode: string | null;
}

/** The real P6/P7 propose -> checkout sequence, shared by the primary
 * purchase (QuoteDrawer) and the upsell purchase (ChatPanel) so there is
 * exactly one client-side call shape for "buy this quote" -- never a
 * second, slightly-different reimplementation. */
export function usePurchaseFlow() {
  const propose = useProposeOrder();
  const checkout = useCheckout();
  const { addTrip, setActiveOrder } = useJourney();

  async function purchase(params: PurchaseParams): Promise<PurchaseResult> {
    const proposed = await propose.mutateAsync({
      quote_id: params.quoteId,
      mandate_id: params.mandateId,
    });

    if (proposed.decision === "reject") {
      return { ok: false, orderId: null, reasonCode: proposed.reason_code };
    }

    setActiveOrder({ orderId: proposed.order_id, sagaId: proposed.saga_id });
    const result = await checkout.mutateAsync({
      order_id: proposed.order_id,
      saga_id: proposed.saga_id,
    });
    const settled = result.status === "COMPLETED";

    addTrip({
      orderId: proposed.order_id,
      sku: params.sku,
      hotelName: params.hotelName,
      totalMinor: params.totalMinor,
      status: settled ? "CAPTURED" : result.status,
      createdAt: Date.now(),
    });

    return {
      ok: settled,
      orderId: proposed.order_id,
      reasonCode: settled ? null : (result.reason_code ?? result.status),
    };
  }

  return { purchase, isPending: propose.isPending || checkout.isPending };
}
