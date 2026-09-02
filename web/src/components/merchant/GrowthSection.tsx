import { useMerchantKpis } from "../../api/merchantHooks";
import { formatMinor } from "../../lib/money";
import type { GrowthArmMetrics } from "../../api/merchantTypes";
import { GrowthChart, MIN_SESSIONS_PER_ARM } from "./GrowthChart";

const ROWS: { key: keyof GrowthArmMetrics; label: string; format: (arm: GrowthArmMetrics) => string }[] = [
  { key: "sessions", label: "Sessions", format: (a) => String(a.sessions) },
  { key: "orders", label: "Orders", format: (a) => String(a.orders) },
  { key: "conversion_rate", label: "Conversion", format: (a) => `${(a.conversion_rate * 100).toFixed(1)}%` },
  { key: "aov_minor", label: "Avg order value", format: (a) => (a.aov_minor != null ? formatMinor(a.aov_minor) : "—") },
  { key: "upsell_offered", label: "Upsells offered", format: (a) => String(a.upsell_offered) },
  { key: "upsell_accepted", label: "Upsells accepted", format: (a) => String(a.upsell_accepted) },
  { key: "attach_rate", label: "Attach rate", format: (a) => (a.attach_rate != null ? `${(a.attach_rate * 100).toFixed(1)}%` : "—") },
];

export function GrowthSection() {
  const kpis = useMerchantKpis();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-navy-900">Growth</h1>
        <p className="mt-1 text-sm text-navy-500">Baseline vs. ACTL Upsell, arm by arm.</p>
      </div>

      {kpis.isLoading && <div className="h-64 animate-pulse rounded-2xl bg-white shadow-card" />}
      {kpis.isError && (
        <div className="rounded-2xl border border-coral-100 bg-coral-100/40 p-6 text-center text-sm text-navy-700">
          Couldn't load growth data.
        </div>
      )}
      {kpis.data && (
        <>
          <GrowthChart baseline={kpis.data.baseline} upsell={kpis.data.upsell} />

          {kpis.data.baseline.sessions >= MIN_SESSIONS_PER_ARM &&
            kpis.data.upsell.sessions >= MIN_SESSIONS_PER_ARM && (
              <div className="overflow-x-auto rounded-2xl border border-sky-100 bg-white shadow-card">
                <table className="w-full min-w-[420px] text-left text-sm">
                  <thead>
                    <tr className="border-b border-sky-100 text-xs font-semibold uppercase tracking-wide text-navy-500">
                      <th scope="col" className="px-4 py-3">
                        Metric
                      </th>
                      <th scope="col" className="px-4 py-3">
                        Baseline
                      </th>
                      <th scope="col" className="px-4 py-3">
                        ACTL Upsell
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {ROWS.map((row) => (
                      <tr key={row.key} className="border-b border-sky-100 last:border-0">
                        <th scope="row" className="px-4 py-3 font-medium text-navy-700">
                          {row.label}
                        </th>
                        <td className="px-4 py-3 text-navy-900">{row.format(kpis.data.baseline)}</td>
                        <td className="px-4 py-3 font-medium text-ocean-600">
                          {row.format(kpis.data.upsell)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
        </>
      )}
    </div>
  );
}
