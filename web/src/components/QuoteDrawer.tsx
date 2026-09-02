import { motion } from "framer-motion";
import { CheckCircle2, Clock, ShieldAlert, X } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useCatalog, useCreateQuote } from "../api/hooks";
import { hotelDisplayName } from "../lib/hotelDisplay";
import { formatMinor } from "../lib/money";
import { useCountdown } from "../lib/useCountdown";
import { useJourney } from "../state/journeyContext";
import { usePurchaseFlow } from "../state/usePurchaseFlow";
import { Overlay } from "./Overlay";

const REASON_MESSAGES: Record<string, string> = {
  STALE_PRICE: "This price changed since your quote was issued. Get a fresh quote to continue.",
  QUOTE_EXPIRED: "This quote expired. Get a fresh quote to continue.",
  UNIT_CAP_EXCEEDED: "This stay's nightly price is above your mandate's max/night cap.",
  BUDGET_EXCEEDED: "This stay's total is above your mandate's total budget cap.",
  MANDATE_EXPIRED: "Your mandate has expired. Start a new mandate to continue.",
  MANDATE_INVALID: "Your mandate can't be used for this purchase. Start a new mandate to continue.",
  MANDATE_REVOKED: "Your mandate was revoked. Start a new mandate to continue.",
  PROVIDER_DECLINED: "The payment was declined. Please try again.",
};

function reasonMessage(code: string | null): string {
  if (!code) return "This purchase couldn't be completed.";
  return REASON_MESSAGES[code] ?? `Purchase declined (${code}).`;
}

export function QuoteDrawer() {
  const { filters, mandate, quote, setQuote, quoteDrawerOpen, setQuoteDrawerOpen } = useJourney();
  const navigate = useNavigate();
  const catalog = useCatalog(mandate?.mandate_id ?? null);
  const createQuote = useCreateQuote();
  const { purchase, isPending } = usePurchaseFlow();
  const [result, setResult] = useState<{ ok: boolean; orderId: string | null; reasonCode: string | null } | null>(
    null,
  );

  // Reset the last checkout result whenever a *new* quote is loaded, so
  // reopening the drawer for a different quote never briefly shows the
  // previous one's outcome. Adjusted during render (React's own pattern
  // for "reset state when a value changes"), not an effect: there's
  // nothing external to synchronize with here, just local state that
  // depends on which quote is currently loaded.
  const [lastQuoteId, setLastQuoteId] = useState(quote?.quote_id);
  if (quote?.quote_id !== lastQuoteId) {
    setLastQuoteId(quote?.quote_id);
    setResult(null);
  }

  const item = quote ? catalog.data?.items.find((i) => i.sku === quote.sku) : undefined;
  const countdown = useCountdown(quote?.expires_at ?? null);

  if (!quote || !item || !mandate) return null;

  const withinMandate =
    quote.total_minor <= mandate.bounds.max_total_minor && quote.unit_price_minor <= mandate.bounds.max_unit_minor;

  const handleRefresh = async () => {
    const fresh = await createQuote.mutateAsync({
      sku: quote.sku,
      mandate_id: mandate.mandate_id,
      nights: filters.nights,
    });
    setQuote(fresh);
    setResult(null);
  };

  const handleCheckout = async () => {
    const outcome = await purchase({
      quoteId: quote.quote_id,
      mandateId: mandate.mandate_id,
      sku: quote.sku,
      hotelName: hotelDisplayName(quote.sku),
      totalMinor: quote.total_minor,
    });
    setResult(outcome);
  };

  return (
    <Overlay open={quoteDrawerOpen} onClose={() => setQuoteDrawerOpen(false)}>
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-label="Quote review"
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", stiffness: 320, damping: 32 }}
            className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col overflow-hidden bg-white shadow-float sm:rounded-l-3xl"
          >
            <div className="flex items-center justify-between border-b border-sky-100 px-5 py-4">
              <h2 className="text-base font-semibold text-navy-900">Your quote</h2>
              <button
                type="button"
                onClick={() => setQuoteDrawerOpen(false)}
                aria-label="Close quote"
                className="flex h-8 w-8 items-center justify-center rounded-full text-navy-500 hover:bg-sky-50"
              >
                <X size={16} />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-5">
              {!result || result.ok === false ? (
                <>
                  <h3 className="text-lg font-semibold text-navy-900">{hotelDisplayName(quote.sku)}</h3>
                  <p className="text-sm text-navy-500">
                    {item.location.city}, {item.location.country} · {quote.nights} nights · {filters.guests} guests
                  </p>

                  <div className="mt-4 flex items-center gap-2 text-sm">
                    {quote.refundable ? (
                      <span className="rounded-full bg-emerald-100 px-2.5 py-0.5 font-medium text-emerald-600">
                        Refundable
                      </span>
                    ) : (
                      <span className="rounded-full bg-sky-100 px-2.5 py-0.5 font-medium text-navy-500">
                        Non-refundable
                      </span>
                    )}
                    <span
                      className={`flex items-center gap-1 rounded-full px-2.5 py-0.5 font-medium ${
                        withinMandate ? "bg-emerald-100 text-emerald-600" : "bg-coral-100 text-coral-600"
                      }`}
                    >
                      {withinMandate ? <CheckCircle2 size={13} /> : <ShieldAlert size={13} />}
                      {withinMandate ? "Within your mandate" : "Outside your mandate"}
                    </span>
                  </div>

                  <div className="my-5 h-px bg-sky-100" />

                  <dl className="space-y-2 text-sm">
                    <div className="flex justify-between">
                      <dt className="text-navy-500">Price per night</dt>
                      <dd className="font-medium text-navy-900">{formatMinor(quote.unit_price_minor)}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-navy-500">Nights</dt>
                      <dd className="font-medium text-navy-900">{quote.nights}</dd>
                    </div>
                    <div className="flex justify-between text-base">
                      <dt className="font-semibold text-navy-900">Total</dt>
                      <dd className="font-semibold text-navy-900">{formatMinor(quote.total_minor)}</dd>
                    </div>
                  </dl>

                  <div
                    className={`mt-5 flex items-center gap-2 rounded-xl px-3 py-2.5 text-sm ${
                      countdown.isExpired ? "bg-coral-100 text-coral-600" : "bg-sky-50 text-navy-700"
                    }`}
                  >
                    <Clock size={15} />
                    {countdown.isExpired ? "This quote has expired." : `Quote expires in ${countdown.label}`}
                  </div>

                  {result?.ok === false && (
                    <div className="mt-4 rounded-xl border border-coral-100 bg-coral-100/50 p-3 text-sm text-navy-900">
                      <p className="font-medium">Purchase declined</p>
                      <p className="mt-1 text-navy-700">{reasonMessage(result.reasonCode)}</p>
                    </div>
                  )}

                  <div className="mt-6 space-y-2">
                    {countdown.isExpired ||
                    result?.reasonCode === "STALE_PRICE" ||
                    result?.reasonCode === "QUOTE_EXPIRED" ? (
                      <button
                        type="button"
                        onClick={handleRefresh}
                        disabled={createQuote.isPending}
                        className="w-full rounded-xl bg-ocean-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-ocean-500 disabled:opacity-60"
                      >
                        {createQuote.isPending ? "Refreshing…" : "Get a fresh quote"}
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={handleCheckout}
                        disabled={isPending}
                        className="w-full rounded-xl bg-coral-500 px-4 py-2.5 text-sm font-semibold text-white hover:bg-coral-600 disabled:opacity-60"
                      >
                        {isPending ? "Securing checkout…" : "Continue to secure checkout"}
                      </button>
                    )}
                  </div>
                </>
              ) : (
                <div className="flex flex-col items-center py-6 text-center">
                  <span className="flex h-14 w-14 items-center justify-center rounded-full bg-emerald-100">
                    <CheckCircle2 size={28} className="text-emerald-600" />
                  </span>
                  <h3 className="mt-4 text-lg font-semibold text-navy-900">Booking confirmed</h3>
                  <p className="mt-1 text-sm text-navy-500">
                    {hotelDisplayName(quote.sku)} · {formatMinor(quote.total_minor)} settled
                  </p>
                  <div className="mt-6 flex w-full flex-col gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        if (result.orderId) navigate(`/merchant?order_id=${result.orderId}&panel=proof`);
                        setQuoteDrawerOpen(false);
                      }}
                      className="w-full rounded-xl bg-ocean-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-ocean-500"
                    >
                      View proof
                    </button>
                  </div>
                </div>
              )}
            </div>
          </motion.div>
    </Overlay>
  );
}
