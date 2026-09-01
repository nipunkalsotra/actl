import { ShieldCheck } from "lucide-react";

const ITEMS = ["Budget-protected booking", "Refund rules respected", "No hidden add-ons"];

export function TrustStrip() {
  return (
    <div className="border-b border-sky-100 bg-sky-50">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-center gap-x-2 gap-y-1 px-4 py-2 text-xs font-medium text-navy-700 sm:text-sm">
        {ITEMS.map((item, i) => (
          <span key={item} className="flex items-center gap-1.5">
            <ShieldCheck size={14} className="text-emerald-600" />
            {item}
            {i < ITEMS.length - 1 && <span className="ml-2 text-sky-100 sm:inline hidden">·</span>}
          </span>
        ))}
      </div>
    </div>
  );
}
