import { motion, AnimatePresence } from "framer-motion";
import { useCatalog, useCreateQuote } from "../api/hooks";
import { formatMinor } from "../lib/money";
import { hotelDisplayName } from "../lib/hotelDisplay";
import { useJourney } from "../state/journeyContext";

export function StickyTripBar() {
  const { filters, mandate, selectedSku, quote, setQuote, setChatOpen, setQuoteDrawerOpen } = useJourney();
  const catalog = useCatalog(mandate?.mandate_id ?? null);
  const createQuote = useCreateQuote();

  const item = catalog.data?.items.find((i) => i.sku === selectedSku);

  if (!selectedSku || !item) return null;

  const estimatedTotal = item.unit_price_minor * filters.nights;
  const showingRealQuote = quote !== null && quote.sku === selectedSku;

  const handleContinue = async () => {
    if (!mandate) {
      setChatOpen(true);
      return;
    }
    if (!showingRealQuote) {
      try {
        const fresh = await createQuote.mutateAsync({
          sku: selectedSku,
          mandate_id: mandate.mandate_id,
          nights: filters.nights,
        });
        setQuote(fresh);
      } catch {
        return;
      }
    }
    setQuoteDrawerOpen(true);
  };

  return (
    <AnimatePresence>
      <motion.div
        initial={{ y: 80, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        exit={{ y: 80, opacity: 0 }}
        transition={{ type: "spring", stiffness: 300, damping: 30 }}
        className="fixed inset-x-0 bottom-0 z-20 border-t border-sky-100 bg-white/95 backdrop-blur"
      >
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3 px-4 py-3 sm:px-6">
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-navy-900">
              Selected: {hotelDisplayName(item.sku)}
            </p>
            <p className="text-xs text-navy-500">
              {showingRealQuote
                ? `${formatMinor(quote!.total_minor)} total`
                : `est. ${formatMinor(estimatedTotal)} total`}{" "}
              · {item.policy.refundable ? "Refundable" : "Non-refundable"}
            </p>
          </div>
          <button
            type="button"
            onClick={handleContinue}
            className="rounded-full border border-sky-100 px-4 py-2.5 text-sm font-medium text-navy-700 hover:bg-sky-50"
          >
            View quote
          </button>
          <button
            type="button"
            onClick={handleContinue}
            disabled={createQuote.isPending}
            className="rounded-full bg-coral-500 px-5 py-2.5 text-sm font-semibold text-white hover:bg-coral-600 disabled:opacity-60"
          >
            {createQuote.isPending ? "Fetching quote…" : "Continue"}
          </button>
        </div>
      </motion.div>
    </AnimatePresence>
  );
}
