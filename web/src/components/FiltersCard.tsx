import { Minus, Plus } from "lucide-react";
import { formatMinor } from "../lib/money";
import { useJourney } from "../state/journeyContext";

const RATING_OPTIONS = [0, 3, 4, 4.5];

function Stepper({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
}) {
  return (
    <div>
      <span className="mb-1.5 block text-xs font-medium text-navy-500">{label}</span>
      <div className="flex items-center justify-between rounded-xl border border-sky-100 bg-sky-50 px-2 py-1.5">
        <button
          type="button"
          aria-label={`Decrease ${label.toLowerCase()}`}
          disabled={value <= min}
          onClick={() => onChange(Math.max(min, value - 1))}
          className="flex h-7 w-7 items-center justify-center rounded-lg bg-white text-navy-700 shadow-sm disabled:opacity-40"
        >
          <Minus size={14} />
        </button>
        <span className="text-sm font-medium text-navy-900">
          {value} {label === "Nights" ? (value === 1 ? "night" : "nights") : value === 1 ? "guest" : "guests"}
        </span>
        <button
          type="button"
          aria-label={`Increase ${label.toLowerCase()}`}
          disabled={value >= max}
          onClick={() => onChange(Math.min(max, value + 1))}
          className="flex h-7 w-7 items-center justify-center rounded-lg bg-white text-navy-700 shadow-sm disabled:opacity-40"
        >
          <Plus size={14} />
        </button>
      </div>
    </div>
  );
}

export function FiltersCard() {
  const { filters, setFilters, mandate } = useJourney();
  const locked = mandate !== null;

  return (
    <div className="rounded-2xl border border-sky-100 bg-white p-5 shadow-card">
      <h2 className="mb-4 text-base font-semibold text-navy-900">Your Goa trip</h2>

      <div className="space-y-4">
        <div>
          <span className="mb-1.5 block text-xs font-medium text-navy-500">Destination</span>
          <div className="rounded-xl border border-sky-100 bg-sky-50 px-3 py-2 text-sm font-medium text-navy-900">
            Goa
          </div>
        </div>

        <div className="grid grid-cols-1 gap-3">
          <Stepper
            label="Nights"
            value={filters.nights}
            min={1}
            max={14}
            onChange={(nights) => setFilters((f) => ({ ...f, nights }))}
          />
          <Stepper
            label="Guests"
            value={filters.guests}
            min={1}
            max={8}
            onChange={(guests) => setFilters((f) => ({ ...f, guests }))}
          />
        </div>

        <div>
          <div className="mb-1.5 flex items-baseline justify-between">
            <span className="text-xs font-medium text-navy-500">Total budget</span>
            <span className="text-xs font-medium text-navy-900">
              up to {formatMinor(filters.budgetMaxMinor)}
            </span>
          </div>
          <input
            type="range"
            min={100_000}
            max={10_000_000}
            step={50_000}
            value={filters.budgetMaxMinor}
            onChange={(e) => setFilters((f) => ({ ...f, budgetMaxMinor: Number(e.target.value) }))}
            className="w-full accent-ocean-600"
            aria-label="Maximum total budget"
          />
        </div>

        <label className="flex cursor-pointer items-center justify-between rounded-xl border border-sky-100 px-3 py-2.5">
          <span className="text-sm font-medium text-navy-900">Refundable only</span>
          <input
            type="checkbox"
            checked={filters.refundableOnly}
            onChange={(e) => setFilters((f) => ({ ...f, refundableOnly: e.target.checked }))}
            className="h-4 w-4 accent-ocean-600"
          />
        </label>

        <div>
          <span className="mb-1.5 block text-xs font-medium text-navy-500">Minimum rating</span>
          <div className="flex flex-wrap gap-1.5">
            {RATING_OPTIONS.map((rating) => (
              <button
                key={rating}
                type="button"
                onClick={() => setFilters((f) => ({ ...f, minRating: rating }))}
                className={`rounded-full px-3 py-1.5 text-xs font-medium transition-colors ${
                  filters.minRating === rating
                    ? "bg-ocean-600 text-white"
                    : "bg-sky-50 text-navy-700 hover:bg-sky-100"
                }`}
              >
                {rating === 0 ? "Any" : `${rating}+`}
              </button>
            ))}
          </div>
        </div>

        {locked && (
          <p className="rounded-lg bg-sky-50 px-3 py-2 text-xs text-navy-500">
            Your mandate is locked. Filters still refine what you browse, but budget changes here
            won't alter your locked mandate.
          </p>
        )}
      </div>
    </div>
  );
}
