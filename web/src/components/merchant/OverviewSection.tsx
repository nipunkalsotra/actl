import { RefreshCw, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { useMerchantKpis } from "../../api/merchantHooks";
import { formatMinor } from "../../lib/money";
import { GrowthChart, MIN_SESSIONS_PER_ARM } from "./GrowthChart";
import { HowItWorksModal } from "./HowItWorksModal";
import { KpiCard } from "./KpiCard";

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function timeAgo(ms: number): string {
  const seconds = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  return `${minutes}m ago`;
}

export function OverviewSection() {
  const kpis = useMerchantKpis();
  const [howItWorksOpen, setHowItWorksOpen] = useState(false);

  const hasGrowthData = kpis.data
    ? kpis.data.baseline.sessions >= MIN_SESSIONS_PER_ARM &&
      kpis.data.upsell.sessions >= MIN_SESSIONS_PER_ARM
    : false;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-navy-900 sm:text-3xl">
            Growth, within customer consent.
          </h1>
          <p className="mt-1 text-sm text-navy-500">
            Every offer is bounded by a signed travel mandate.
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs text-navy-500">
          {kpis.dataUpdatedAt > 0 && <span>Updated {timeAgo(kpis.dataUpdatedAt)}</span>}
          <button
            type="button"
            onClick={() => kpis.refetch()}
            disabled={kpis.isFetching}
            className="flex items-center gap-1.5 rounded-full border border-sky-100 bg-white px-3 py-1.5 font-medium text-navy-700 hover:bg-sky-50 disabled:opacity-60"
          >
            <RefreshCw size={13} className={kpis.isFetching ? "animate-spin" : undefined} />
            Refresh
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
        <KpiCard
          label="Revenue uplift (simulated)"
          value={hasGrowthData && kpis.data?.revenue_uplift != null ? formatPercent(kpis.data.revenue_uplift) : null}
          context="vs baseline"
          isLoading={kpis.isLoading}
          isError={kpis.isError}
          emptyHint={`Not enough completed sessions to compare yet (need ${MIN_SESSIONS_PER_ARM}+ per arm)`}
        />
        <KpiCard
          label="Conversion (simulated)"
          value={hasGrowthData && kpis.data ? formatPercent(kpis.data.upsell.conversion_rate) : null}
          context={hasGrowthData && kpis.data ? `vs ${formatPercent(kpis.data.baseline.conversion_rate)} baseline` : undefined}
          isLoading={kpis.isLoading}
          isError={kpis.isError}
          emptyHint={`Not enough completed sessions to compare yet (need ${MIN_SESSIONS_PER_ARM}+ per arm)`}
        />
        <KpiCard
          label="Avg order value (simulated)"
          value={
            hasGrowthData && kpis.data?.upsell.aov_minor != null
              ? formatMinor(kpis.data.upsell.aov_minor)
              : null
          }
          context={
            hasGrowthData && kpis.data?.baseline.aov_minor != null
              ? `vs ${formatMinor(kpis.data.baseline.aov_minor)} baseline`
              : undefined
          }
          isLoading={kpis.isLoading}
          isError={kpis.isError}
          emptyHint={`Not enough completed sessions to compare yet (need ${MIN_SESSIONS_PER_ARM}+ per arm)`}
        />
        <KpiCard
          label="Upsell attach (simulated)"
          value={
            hasGrowthData && kpis.data?.upsell.attach_rate != null
              ? formatPercent(kpis.data.upsell.attach_rate)
              : null
          }
          context="of upsell offers"
          isLoading={kpis.isLoading}
          isError={kpis.isError}
          emptyHint={`Not enough completed sessions to compare yet (need ${MIN_SESSIONS_PER_ARM}+ per arm)`}
        />
        <KpiCard
          label="Protected offers blocked"
          value={kpis.data ? String(kpis.data.protected_offers_blocked) : null}
          context="denied by policy"
          isLoading={kpis.isLoading}
          isError={kpis.isError}
        />
      </div>

      <div>
        <h2 className="mb-3 text-base font-semibold text-navy-900">Real upsell activity</h2>
        <p className="mb-3 -mt-2 text-xs text-navy-500">
          Real buyer-driven post-booking add-on purchases -- separate from the simulated growth
          arms above, never blended into that comparison.
        </p>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
          <KpiCard
            label="Offered"
            value={kpis.data ? String(kpis.data.real_upsell.offered) : null}
            context="eligible offers shown"
            isLoading={kpis.isLoading}
            isError={kpis.isError}
          />
          <KpiCard
            label="Accepted"
            value={kpis.data ? String(kpis.data.real_upsell.accepted) : null}
            context="buyer approved"
            isLoading={kpis.isLoading}
            isError={kpis.isError}
          />
          <KpiCard
            label="Settled"
            value={kpis.data ? String(kpis.data.real_upsell.settled) : null}
            context="paid successfully"
            isLoading={kpis.isLoading}
            isError={kpis.isError}
          />
          <KpiCard
            label="Attach rate"
            value={
              kpis.data?.real_upsell.attach_rate != null
                ? formatPercent(kpis.data.real_upsell.attach_rate)
                : null
            }
            context="settled / offered"
            isLoading={kpis.isLoading}
            isError={kpis.isError}
            emptyHint="No offers shown yet"
          />
          <KpiCard
            label="Organic gross sales"
            value={kpis.data ? formatMinor(kpis.data.organic.gross_sales_minor) : null}
            context={kpis.data ? `${kpis.data.organic.orders} completed orders` : undefined}
            isLoading={kpis.isLoading}
            isError={kpis.isError}
          />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[2fr_1fr]">
        <div>
          <h2 className="mb-3 text-base font-semibold text-navy-900">ACTL growth impact</h2>
          {kpis.isLoading ? (
            <div className="h-64 animate-pulse rounded-2xl bg-white shadow-card" />
          ) : kpis.data ? (
            <GrowthChart baseline={kpis.data.baseline} upsell={kpis.data.upsell} />
          ) : (
            <div className="flex h-64 items-center justify-center rounded-2xl border border-coral-100 bg-coral-100/40 text-sm text-navy-700">
              Couldn't load growth data.
            </div>
          )}
        </div>

        <div className="flex flex-col items-center justify-between rounded-2xl border border-sky-100 bg-white p-6 text-center shadow-card">
          <span className="flex h-20 w-20 items-center justify-center rounded-full bg-emerald-100">
            <ShieldCheck size={36} className="text-emerald-600" />
          </span>
          <p className="mt-6 text-base font-semibold text-navy-900">
            Upsells are separately approved. Never auto-charged.
          </p>
          <button
            type="button"
            onClick={() => setHowItWorksOpen(true)}
            className="mt-4 self-start rounded-full bg-ocean-600 px-4 py-2 text-sm font-semibold text-white hover:bg-ocean-500"
          >
            How it works
          </button>
        </div>
      </div>

      <HowItWorksModal open={howItWorksOpen} onClose={() => setHowItWorksOpen(false)} />
    </div>
  );
}
