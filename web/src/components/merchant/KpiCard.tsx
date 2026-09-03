import { CircleAlert, Minus } from "lucide-react";

interface KpiCardProps {
  label: string;
  value: string | null;
  context?: string;
  isLoading: boolean;
  isError: boolean;
  emptyHint?: string;
}

export function KpiCard({ label, value, context, isLoading, isError, emptyHint }: KpiCardProps) {
  return (
    <div className="rounded-2xl border border-sky-100 bg-card p-5 shadow-card">
      <p className="text-sm font-medium text-navy-500">{label}</p>

      {isLoading && (
        <div className="mt-3 space-y-2" aria-hidden="true">
          <div className="h-7 w-20 animate-pulse rounded bg-sky-100" />
          <div className="h-3 w-24 animate-pulse rounded bg-sky-100" />
        </div>
      )}

      {!isLoading && isError && (
        <p className="mt-3 flex items-center gap-1.5 text-sm text-coral-400">
          <CircleAlert size={14} /> Couldn't load
        </p>
      )}

      {!isLoading && !isError && value === null && (
        <div className="mt-3">
          <p className="text-2xl font-semibold text-navy-300">—</p>
          <p className="mt-1 text-xs text-navy-500">{emptyHint ?? "No data yet"}</p>
        </div>
      )}

      {!isLoading && !isError && value !== null && (
        <div className="mt-3">
          <p className="text-2xl font-semibold text-navy-900">{value}</p>
          {context && <p className="mt-1 text-xs text-navy-500">{context}</p>}
          {/* No stored time-series exists for this metric -- a restrained
              neutral marker, never an invented trend line. */}
          <div className="mt-3 flex items-center gap-1 text-sky-100" aria-hidden="true">
            <Minus size={40} strokeWidth={3} />
          </div>
        </div>
      )}
    </div>
  );
}
