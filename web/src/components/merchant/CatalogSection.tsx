import { Star } from "lucide-react";
import { useCatalog } from "../../api/hooks";
import { hotelDisplayName } from "../../lib/hotelDisplay";
import { formatMinor } from "../../lib/money";

export function CatalogSection() {
  const catalog = useCatalog(null);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold text-navy-900">Catalog</h1>
          <p className="mt-1 text-sm text-navy-500">
            Demo partner inventory. Read-only here -- price changes are a separate, audited flow.
          </p>
        </div>
        {catalog.data && (
          <p className="text-xs text-navy-500">
            Catalog version {catalog.data.catalog_version} · as of{" "}
            {new Date(catalog.data.generated_at).toLocaleString()}
          </p>
        )}
      </div>

      {catalog.isLoading && <div className="h-48 animate-pulse rounded-2xl bg-card shadow-card" />}
      {catalog.isError && (
        <div className="rounded-2xl border border-coral-100 bg-coral-100/40 p-6 text-center text-sm text-navy-700">
          Couldn't load the catalog.
        </div>
      )}

      {catalog.data && (
        <div className="overflow-x-auto rounded-2xl border border-sky-100 bg-card shadow-card">
          <table className="w-full min-w-[560px] text-left text-sm">
            <thead>
              <tr className="border-b border-sky-100 text-xs font-semibold uppercase tracking-wide text-navy-500">
                <th scope="col" className="px-4 py-3">Stay / SKU</th>
                <th scope="col" className="px-4 py-3">Unit price</th>
                <th scope="col" className="px-4 py-3">Refundable</th>
                <th scope="col" className="px-4 py-3">Rating</th>
                <th scope="col" className="px-4 py-3">Version</th>
              </tr>
            </thead>
            <tbody>
              {catalog.data.items.map((item) => (
                <tr key={item.sku} className="border-b border-sky-100 last:border-0">
                  <td className="px-4 py-3">
                    <p className="font-medium text-navy-900">{hotelDisplayName(item.sku)}</p>
                    <p className="font-mono text-xs text-navy-500">{item.sku}</p>
                  </td>
                  <td className="px-4 py-3 text-navy-900">{formatMinor(item.unit_price_minor)}</td>
                  <td className="px-4 py-3">
                    <span
                      className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
                        item.policy.refundable
                          ? "bg-emerald-100 text-emerald-600"
                          : "bg-sky-100 text-navy-500"
                      }`}
                    >
                      {item.policy.refundable ? "Refundable" : "Non-refundable"}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className="flex items-center gap-1 text-navy-700">
                      <Star size={13} className="fill-coral-500 text-coral-500" />
                      {item.attributes.rating.toFixed(1)}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-navy-500">v{item.version}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
