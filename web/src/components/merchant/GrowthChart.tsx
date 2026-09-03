import { formatMinor } from "../../lib/money";
import type { GrowthArmMetrics } from "../../api/merchantTypes";

interface Metric {
  label: string;
  baseline: number;
  upsell: number;
  format: (value: number) => string;
}

function buildMetrics(baseline: GrowthArmMetrics, upsell: GrowthArmMetrics): Metric[] {
  const baselineRevenue = (baseline.aov_minor ?? 0) * baseline.orders;
  const upsellRevenue = (upsell.aov_minor ?? 0) * upsell.orders;
  return [
    {
      label: "Conversion",
      baseline: baseline.conversion_rate * 100,
      upsell: upsell.conversion_rate * 100,
      format: (v) => `${v.toFixed(1)}%`,
    },
    {
      label: "AOV",
      baseline: baseline.aov_minor ?? 0,
      upsell: upsell.aov_minor ?? 0,
      format: (v) => formatMinor(v),
    },
    {
      label: "Revenue",
      baseline: baselineRevenue,
      upsell: upsellRevenue,
      format: (v) => formatMinor(v),
    },
  ];
}

function Bars({ metric }: { metric: Metric }) {
  const max = Math.max(metric.baseline, metric.upsell, 1);
  const baselineHeight = Math.max((metric.baseline / max) * 100, 2);
  const upsellHeight = Math.max((metric.upsell / max) * 100, 2);

  return (
    <div className="flex flex-1 flex-col items-center gap-2">
      <div className="flex h-40 w-full items-end justify-center gap-3">
        <div className="flex h-full flex-col items-center justify-end gap-1">
          <span className="text-xs font-semibold text-navy-700">{metric.format(metric.baseline)}</span>
          <div
            className="w-9 rounded-t-lg bg-sky-100 sm:w-12"
            style={{ height: `${baselineHeight}%` }}
          />
        </div>
        <div className="flex h-full flex-col items-center justify-end gap-1">
          <span className="text-xs font-semibold text-ocean-400">{metric.format(metric.upsell)}</span>
          <div
            className="w-9 rounded-t-lg bg-ocean-600 sm:w-12"
            style={{ height: `${upsellHeight}%` }}
          />
        </div>
      </div>
      <p className="text-sm font-medium text-navy-700">{metric.label}</p>
    </div>
  );
}

// A conversion-rate/AOV comparison between two arms is not meaningful
// below a minimum sample size -- an 8-vs-3-session "70% vs 33%"
// comparison would just be noise dressed up as a finding.
export const MIN_SESSIONS_PER_ARM = 10;

interface GrowthChartProps {
  baseline: GrowthArmMetrics;
  upsell: GrowthArmMetrics;
}

export function GrowthChart({ baseline, upsell }: GrowthChartProps) {
  const hasEnoughData =
    baseline.sessions >= MIN_SESSIONS_PER_ARM && upsell.sessions >= MIN_SESSIONS_PER_ARM;
  const metrics = buildMetrics(baseline, upsell);

  if (!hasEnoughData) {
    return (
      <div className="flex h-64 flex-col items-center justify-center rounded-2xl border border-sky-100 bg-card text-center shadow-card">
        <p className="text-sm font-medium text-navy-700">Not enough completed sessions to compare yet</p>
        <p className="mt-1 max-w-xs text-xs text-navy-500">
          Needs at least {MIN_SESSIONS_PER_ARM} completed sessions in each arm (currently baseline:{" "}
          {baseline.sessions}, upsell: {upsell.sessions}). Run{" "}
          <code className="rounded bg-sky-50 px-1 py-0.5">actl growth</code> to generate more.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-sky-100 bg-card p-5 shadow-card">
      <div className="mb-4 flex items-center gap-4 text-xs font-medium text-navy-500">
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm bg-sky-100" /> Baseline
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm bg-ocean-600" /> ACTL Upsell
        </span>
      </div>

      <div className="flex items-end gap-2" role="img" aria-label="Baseline versus ACTL upsell growth comparison">
        {metrics.map((metric) => (
          <Bars key={metric.label} metric={metric} />
        ))}
      </div>

      {/* Accessible data table -- the real numbers behind the bars above,
          for screen readers and anyone who wants the exact figures. */}
      <table className="sr-only">
        <caption>Baseline vs ACTL upsell growth impact</caption>
        <thead>
          <tr>
            <th scope="col">Metric</th>
            <th scope="col">Baseline</th>
            <th scope="col">ACTL Upsell</th>
          </tr>
        </thead>
        <tbody>
          {metrics.map((metric) => (
            <tr key={metric.label}>
              <th scope="row">{metric.label}</th>
              <td>{metric.format(metric.baseline)}</td>
              <td>{metric.format(metric.upsell)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
