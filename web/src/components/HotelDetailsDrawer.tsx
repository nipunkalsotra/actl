import { motion } from "framer-motion";
import { Coffee, ShieldCheck, Star, Waves, X } from "lucide-react";
import { useCatalog } from "../api/hooks";
import { isWithinTripBudget } from "../lib/feasibility";
import { formatMinor } from "../lib/money";
import { hotelDisplayName, hotelGradient } from "../lib/hotelDisplay";
import { useJourney } from "../state/journeyContext";
import { Overlay } from "./Overlay";

export function HotelDetailsDrawer() {
  const { detailsSku, setDetailsSku, mandate, filters, setSelectedSku } = useJourney();
  const catalog = useCatalog(mandate?.mandate_id ?? null);
  const item = catalog.data?.items.find((i) => i.sku === detailsSku);

  return (
    <Overlay open={detailsSku !== null && item !== undefined} onClose={() => setDetailsSku(null)}>
      {item && (
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-label={`${hotelDisplayName(item.sku)} details`}
            initial={{ opacity: 0, scale: 0.96, y: 12 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96, y: 12 }}
            transition={{ type: "spring", stiffness: 320, damping: 28 }}
            className="fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-3xl bg-card shadow-float"
          >
            <div className={`relative h-40 bg-gradient-to-br ${hotelGradient(item.sku)}`}>
              <button
                type="button"
                onClick={() => setDetailsSku(null)}
                aria-label="Close details"
                className="absolute right-3 top-3 flex h-8 w-8 items-center justify-center rounded-full bg-card/90 text-navy-900 hover:bg-card"
              >
                <X size={16} />
              </button>
            </div>

            <div className="max-h-[60vh] overflow-y-auto p-6">
              <h2 className="text-xl font-semibold text-navy-900">{hotelDisplayName(item.sku)}</h2>
              <p className="mt-0.5 text-sm text-navy-500">
                {item.location.city}, {item.location.country}
              </p>

              <div className="mt-4 flex flex-wrap items-center gap-3 text-sm">
                <span className="flex items-center gap-1 font-medium text-navy-900">
                  <Star size={15} className="fill-coral-500 text-coral-500" />
                  {item.attributes.rating.toFixed(1)}
                </span>
                <span
                  className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
                    item.policy.refundable ? "bg-emerald-100 text-emerald-600" : "bg-sky-100 text-navy-500"
                  }`}
                >
                  {item.policy.refundable ? "Refundable" : "Non-refundable"}
                </span>
                <span className="text-xs text-navy-500">{item.available_units} units available</span>
              </div>

              <div className="mt-4 grid grid-cols-2 gap-3 text-sm text-navy-700">
                {item.attributes.sea_facing && (
                  <span className="flex items-center gap-1.5">
                    <Waves size={15} /> Sea-facing
                  </span>
                )}
                {item.attributes.breakfast_included && (
                  <span className="flex items-center gap-1.5">
                    <Coffee size={15} /> Breakfast included
                  </span>
                )}
                {item.policy.instant_confirm && (
                  <span className="flex items-center gap-1.5">
                    <ShieldCheck size={15} /> Instant confirmation
                  </span>
                )}
                <span className="flex items-center gap-1.5">
                  Cancel up to {item.policy.cancellation_window_h}h before
                </span>
              </div>

              <div className="my-5 h-px bg-sky-100" />

              <div className="flex items-center justify-between">
                <div>
                  <p className="text-lg font-semibold text-navy-900">{formatMinor(item.unit_price_minor)}</p>
                  <p className="text-xs text-navy-500">
                    per night · est. {formatMinor(item.unit_price_minor * filters.nights)} for{" "}
                    {filters.nights} nights
                  </p>
                </div>
                <button
                  type="button"
                  disabled={item.available_units <= 0 || !isWithinTripBudget(item.unit_price_minor, filters.nights, mandate)}
                  onClick={() => {
                    setSelectedSku(item.sku);
                    setDetailsSku(null);
                  }}
                  className="rounded-full bg-coral-500 px-5 py-2.5 text-sm font-semibold text-white hover:bg-coral-600 disabled:cursor-not-allowed disabled:bg-sky-100 disabled:text-navy-500"
                >
                  {item.available_units <= 0
                    ? "Sold out"
                    : !isWithinTripBudget(item.unit_price_minor, filters.nights, mandate)
                      ? "Over your trip budget"
                      : "Select this stay"}
                </button>
              </div>
            </div>
          </motion.div>
      )}
    </Overlay>
  );
}
