import { Coffee, Star, Waves } from "lucide-react";
import { formatMinor } from "../lib/money";
import { hotelDisplayName, hotelGradient, hotelImage } from "../lib/hotelDisplay";
import type { CatalogItem } from "../api/types";

interface HotelCardProps {
  item: CatalogItem;
  nights: number;
  selected: boolean;
  badge?: string;
  /** Real price x current nights exceeds the locked mandate's total cap.
   * Client-side early guard only -- the server-side gate is still the
   * only thing that can actually deny a purchase. */
  overTripBudget: boolean;
  /** True only for the first, above-the-fold card -- every other card's
   * image lazy-loads. */
  eagerImage?: boolean;
  onSelect: () => void;
  onViewDetails: () => void;
}

export function HotelCard({
  item,
  nights,
  selected,
  badge,
  overTripBudget,
  eagerImage,
  onSelect,
  onViewDetails,
}: HotelCardProps) {
  const soldOut = item.available_units <= 0;
  const estimatedTotal = item.unit_price_minor * nights;
  const blocked = (soldOut || overTripBudget) && !selected;
  const image = hotelImage(item.sku);
  const displayName = hotelDisplayName(item.sku);

  return (
    <div
      data-testid={`hotel-card-${item.sku}`}
      className={`overflow-hidden rounded-2xl border shadow-card transition-shadow ${
        selected ? "border-ocean-500 ring-2 ring-ocean-500/30" : "border-sky-100"
      } ${blocked ? "bg-sky-50" : "bg-card"}`}
    >
      <div
        className={`relative h-32 overflow-hidden sm:h-36 ${image ? "bg-sky-100" : `bg-gradient-to-br ${hotelGradient(item.sku)}`}`}
      >
        {image && (
          <img
            src={image}
            alt={`Representative view of ${displayName}`}
            loading={eagerImage ? "eager" : "lazy"}
            decoding="async"
            className="h-full w-full object-cover"
          />
        )}
        {badge && (
          <span className="absolute left-3 top-3 rounded-full bg-ocean-600 px-2.5 py-1 text-xs font-semibold text-white shadow-sm">
            {badge}
          </span>
        )}
        {soldOut ? (
          <span className="absolute right-3 top-3 rounded-full bg-overlay/80 px-2.5 py-1 text-xs font-semibold text-white">
            Sold out
          </span>
        ) : (
          overTripBudget && (
            <span className="absolute right-3 top-3 rounded-full bg-coral-600/90 px-2.5 py-1 text-xs font-semibold text-white">
              Over your trip budget
            </span>
          )
        )}
      </div>

      <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <h3 className="truncate text-base font-semibold text-navy-900">{displayName}</h3>
          <p className="text-xs text-navy-500">
            {item.location.city}, {item.location.country}
          </p>

          <div className="mt-2 flex flex-wrap items-center gap-2">
            {item.policy.refundable ? (
              <span className="flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-600">
                Refundable
              </span>
            ) : (
              <span className="rounded-full bg-sky-100 px-2 py-0.5 text-xs font-medium text-navy-500">
                Non-refundable
              </span>
            )}
            <span className="flex items-center gap-1 text-xs font-medium text-navy-700">
              <Star size={13} className="fill-coral-500 text-coral-500" />
              {item.attributes.rating.toFixed(1)}
            </span>
            {item.attributes.sea_facing && (
              <span className="flex items-center gap-1 text-xs text-navy-500">
                <Waves size={13} /> Sea-facing
              </span>
            )}
            {item.attributes.breakfast_included && (
              <span className="flex items-center gap-1 text-xs text-navy-500">
                <Coffee size={13} /> Breakfast
              </span>
            )}
          </div>
        </div>

        <div className="flex shrink-0 flex-col items-start gap-2 sm:items-end">
          <div className="text-right">
            <p className="text-base font-semibold text-navy-900">{formatMinor(item.unit_price_minor)}</p>
            <p className="text-xs text-navy-500">/night · est. {formatMinor(estimatedTotal)} total</p>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onViewDetails}
              className="rounded-full border border-sky-100 px-3 py-1.5 text-xs font-medium text-navy-700 hover:bg-sky-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ocean-500"
            >
              View details
            </button>
            <button
              type="button"
              data-testid={`hotel-select-${item.sku}`}
              disabled={blocked}
              onClick={onSelect}
              className={`rounded-full px-3 py-1.5 text-xs font-semibold focus-visible:outline focus-visible:outline-2 focus-visible:outline-ocean-500 ${
                selected
                  ? "bg-ocean-600 text-white"
                  : "bg-coral-500 text-white hover:bg-coral-600 disabled:cursor-not-allowed disabled:bg-sky-100 disabled:text-navy-500"
              }`}
            >
              {selected ? "Selected" : soldOut ? "Sold out" : overTripBudget ? "Over budget" : "Select"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
