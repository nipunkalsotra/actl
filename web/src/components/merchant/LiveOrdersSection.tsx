import { Search } from "lucide-react";
import { useMemo, useState } from "react";
import { useMerchantOrders } from "../../api/merchantHooks";
import type { MerchantOrderItem } from "../../api/merchantTypes";
import { formatMinor } from "../../lib/money";

type TrustFilter = "all" | "captured" | "declined" | "released" | "processing";

function trustStatus(order: MerchantOrderItem): { label: string; tone: "green" | "amber" | "coral" | "neutral" } {
  if (order.status === "CAPTURED") return { label: "Captured", tone: "green" };
  if (order.status === "COMPENSATED") return { label: "Released", tone: "coral" };
  if (order.status === "FAILED") return { label: "Declined safely", tone: "coral" };
  return { label: "Processing", tone: "neutral" };
}

function filterMatches(order: MerchantOrderItem, filter: TrustFilter): boolean {
  if (filter === "all") return true;
  const status = trustStatus(order);
  if (filter === "captured") return status.label === "Captured";
  if (filter === "declined") return status.label === "Declined safely";
  if (filter === "released") return status.label === "Released";
  return status.label === "Processing";
}

const TONE_CLASSES: Record<string, string> = {
  green: "bg-emerald-100 text-emerald-600",
  amber: "bg-amber-100 text-amber-700",
  coral: "bg-coral-100 text-coral-600",
  neutral: "bg-sky-100 text-navy-700",
};

interface LiveOrdersSectionProps {
  onOpenOrder: (orderId: string) => void;
}

export function LiveOrdersSection({ onOpenOrder }: LiveOrdersSectionProps) {
  const orders = useMerchantOrders(100);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<TrustFilter>("all");

  const filtered = useMemo(() => {
    const items = orders.data?.items ?? [];
    const q = search.trim().toLowerCase();
    return items.filter((order) => {
      if (!filterMatches(order, filter)) return false;
      if (!q) return true;
      return order.order_id.toLowerCase().includes(q) || (order.sku ?? "").toLowerCase().includes(q);
    });
  }, [orders.data, search, filter]);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold text-navy-900">Live orders</h1>
        <p className="mt-1 text-sm text-navy-500">Real order and payment records from this environment.</p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <label className="relative flex-1 min-w-[200px]">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-navy-500" />
          <span className="sr-only">Search orders</span>
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by order reference or SKU…"
            className="w-full rounded-full border border-sky-100 bg-white py-2 pl-9 pr-3 text-sm text-navy-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ocean-500"
          />
        </label>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter by trust status">
          {(["all", "captured", "declined", "released", "processing"] as TrustFilter[]).map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setFilter(f)}
              className={`rounded-full px-3 py-1.5 text-xs font-medium capitalize transition-colors ${
                filter === f ? "bg-ocean-600 text-white" : "bg-white text-navy-700 shadow-card hover:bg-sky-50"
              }`}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      {orders.isLoading && (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-14 animate-pulse rounded-xl bg-white shadow-card" />
          ))}
        </div>
      )}

      {orders.isError && (
        <div className="rounded-2xl border border-coral-100 bg-coral-100/40 p-6 text-center text-sm text-navy-700">
          Couldn't load orders.
        </div>
      )}

      {!orders.isLoading && !orders.isError && filtered.length === 0 && (
        <div className="rounded-2xl border border-sky-100 bg-white p-8 text-center text-sm text-navy-500 shadow-card">
          No orders match your search.
        </div>
      )}

      {!orders.isLoading && filtered.length > 0 && (
        <div className="overflow-x-auto rounded-2xl border border-sky-100 bg-white shadow-card">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead>
              <tr className="border-b border-sky-100 text-xs font-semibold uppercase tracking-wide text-navy-500">
                <th scope="col" className="px-4 py-3">Order</th>
                <th scope="col" className="px-4 py-3">Stay / SKU</th>
                <th scope="col" className="px-4 py-3">Amount</th>
                <th scope="col" className="px-4 py-3">Trust status</th>
                <th scope="col" className="px-4 py-3">Updated</th>
                <th scope="col" className="px-4 py-3" />
              </tr>
            </thead>
            <tbody>
              {filtered.map((order) => {
                const status = trustStatus(order);
                return (
                  <tr key={order.order_id} className="border-b border-sky-100 last:border-0 hover:bg-sky-50">
                    <td className="px-4 py-3 font-mono text-xs text-navy-700">{order.order_id}</td>
                    <td className="px-4 py-3 text-navy-900">{order.sku ?? "—"}</td>
                    <td className="px-4 py-3 font-medium text-navy-900">{formatMinor(order.amount_minor)}</td>
                    <td className="px-4 py-3">
                      <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${TONE_CLASSES[status.tone]}`}>
                        {status.label}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-xs text-navy-500">
                      {order.created_at ? new Date(order.created_at).toLocaleString() : "—"}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <button
                        type="button"
                        onClick={() => onOpenOrder(order.order_id)}
                        className="rounded-full border border-sky-100 px-3 py-1.5 text-xs font-medium text-navy-700 hover:bg-sky-50"
                      >
                        View
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
