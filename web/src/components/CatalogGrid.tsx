import { useMemo } from "react";
import { useCatalog } from "../api/hooks";
import { isWithinTripBudget } from "../lib/feasibility";
import { useJourney, type SortMode } from "../state/journeyContext";
import type { CatalogItem } from "../api/types";
import { HotelCard } from "./HotelCard";

const SORT_OPTIONS: { mode: SortMode; label: string }[] = [
  { mode: "best_match", label: "Best match" },
  { mode: "price_asc", label: "Price low to high" },
  { mode: "rating_desc", label: "Top rated" },
];

function CardSkeleton() {
  return (
    <div className="animate-pulse overflow-hidden rounded-2xl border border-sky-100 bg-white shadow-card">
      <div className="h-32 bg-sky-100 sm:h-36" />
      <div className="space-y-2 p-4">
        <div className="h-4 w-2/3 rounded bg-sky-100" />
        <div className="h-3 w-1/3 rounded bg-sky-100" />
        <div className="h-3 w-1/2 rounded bg-sky-100" />
      </div>
    </div>
  );
}

export function CatalogGrid() {
  const { filters, sortMode, setSortMode, mandate, selectedSku, setSelectedSku, setDetailsSku } =
    useJourney();
  const catalog = useCatalog(mandate?.mandate_id ?? null);

  const filtered = useMemo(() => {
    const items = catalog.data?.items ?? [];
    return items.filter(
      (item) =>
        item.attributes.rating >= filters.minRating &&
        (!filters.refundableOnly || item.policy.refundable) &&
        item.unit_price_minor * filters.nights <= filters.budgetMaxMinor,
    );
  }, [catalog.data, filters]);

  const sorted = useMemo(() => {
    const items = [...filtered];
    if (sortMode === "price_asc") return items.sort((a, b) => a.unit_price_minor - b.unit_price_minor);
    if (sortMode === "rating_desc") return items.sort((a, b) => b.attributes.rating - a.attributes.rating);
    return items; // best_match: server order (mandate-ranked when locked, price-asc otherwise)
  }, [filtered, sortMode]);

  const bestMatchAvailable = catalog.data?.ranked === true;

  // "Best match" must never land on an item that looks affordable per
  // night but blows the mandate's real total cap for the current trip --
  // the server's own ranking only ever checks the per-night price
  // (domain.agent.buyer.is_feasible), so this is skipped client-side, on
  // top of the server list, using the real price and current nights.
  const bestMatchSku =
    bestMatchAvailable && sortMode === "best_match"
      ? sorted.find((item) => isWithinTripBudget(item.unit_price_minor, filters.nights, mandate))?.sku
      : undefined;

  return (
    <section aria-labelledby="catalog-heading">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 id="catalog-heading" className="text-2xl font-semibold text-navy-900">
            Stays in Goa
          </h1>
          <p className="text-sm text-navy-500" aria-live="polite">
            {catalog.isLoading ? "Loading stays…" : `${sorted.length} stays match your preferences`}
          </p>
        </div>

        <div className="flex flex-wrap gap-2" role="group" aria-label="Sort stays">
          {SORT_OPTIONS.map((opt) => {
            const disabled = opt.mode === "best_match" && !bestMatchAvailable;
            return (
              <button
                key={opt.mode}
                type="button"
                disabled={disabled}
                title={disabled ? "Lock a mandate to unlock deterministic best-match ranking" : undefined}
                onClick={() => setSortMode(opt.mode)}
                className={`rounded-full px-3.5 py-2 text-sm font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-ocean-500 ${
                  sortMode === opt.mode
                    ? "bg-ocean-600 text-white"
                    : "bg-white text-navy-700 shadow-card hover:bg-sky-50"
                } disabled:cursor-not-allowed disabled:opacity-40`}
              >
                {opt.label}
              </button>
            );
          })}
        </div>
      </div>

      {catalog.data?.degraded === true && (
        <p className="mb-3 rounded-lg bg-sky-50 px-3 py-2 text-xs text-navy-500">
          Ranking is running on the deterministic fallback scorer right now, not the LLM — results
          are still real and mandate-filtered.
        </p>
      )}

      {catalog.isError && (
        <div className="rounded-2xl border border-coral-100 bg-coral-100/40 p-5 text-center">
          <p className="mb-3 text-sm font-medium text-navy-900">Couldn't load stays right now.</p>
          <button
            type="button"
            onClick={() => catalog.refetch()}
            className="rounded-full bg-coral-500 px-4 py-2 text-sm font-medium text-white hover:bg-coral-600"
          >
            Retry
          </button>
        </div>
      )}

      {catalog.isLoading && (
        <div className="grid grid-cols-1 gap-4">
          {[0, 1, 2].map((i) => (
            <CardSkeleton key={i} />
          ))}
        </div>
      )}

      {!catalog.isLoading && !catalog.isError && sorted.length === 0 && (
        <div className="rounded-2xl border border-sky-100 bg-white p-8 text-center text-sm text-navy-500 shadow-card">
          No stays match your current filters. Try raising your budget or clearing the rating filter.
        </div>
      )}

      {!catalog.isLoading && sorted.length > 0 && (
        <div className="grid grid-cols-1 gap-4 pb-28">
          {sorted.map((item: CatalogItem) => (
            <HotelCard
              key={item.sku}
              item={item}
              nights={filters.nights}
              selected={selectedSku === item.sku}
              badge={bestMatchSku === item.sku ? "Best match" : undefined}
              overTripBudget={!isWithinTripBudget(item.unit_price_minor, filters.nights, mandate)}
              onSelect={() => setSelectedSku(item.sku)}
              onViewDetails={() => setDetailsSku(item.sku)}
            />
          ))}
        </div>
      )}
    </section>
  );
}
