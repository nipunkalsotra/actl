import { motion } from "framer-motion";
import {
  BarChart3,
  FlaskConical,
  LayoutGrid,
  ReceiptText,
  ShieldCheck,
  Tag,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Overlay } from "../Overlay";
import type { MerchantSection } from "../../pages/merchantSections";

const NAV_ITEMS: { key: MerchantSection; label: string; icon: LucideIcon }[] = [
  { key: "overview", label: "Overview", icon: LayoutGrid },
  { key: "orders", label: "Live orders", icon: ReceiptText },
  { key: "growth", label: "Growth", icon: BarChart3 },
  { key: "catalog", label: "Catalog", icon: Tag },
  { key: "trust", label: "Trust & audit", icon: ShieldCheck },
];

function NavList({
  active,
  onSelect,
}: {
  active: MerchantSection;
  onSelect: (section: MerchantSection) => void;
}) {
  return (
    <nav className="flex flex-1 flex-col gap-1 p-3" aria-label="Merchant sections">
      {NAV_ITEMS.map((item) => {
        const isActive = active === item.key;
        const Icon = item.icon;
        return (
          <button
            key={item.key}
            type="button"
            aria-current={isActive ? "page" : undefined}
            onClick={() => onSelect(item.key)}
            className={`flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-ocean-500 ${
              isActive ? "bg-ocean-600 text-white" : "text-navy-700 hover:bg-sky-50"
            }`}
          >
            <Icon size={18} />
            {item.label}
          </button>
        );
      })}

      <div className="mt-auto rounded-xl border border-coral-100 bg-coral-100/40 p-3">
        <button
          type="button"
          aria-current={active === "demo" ? "page" : undefined}
          onClick={() => onSelect("demo")}
          className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-sm font-semibold focus-visible:outline focus-visible:outline-2 focus-visible:outline-ocean-500 ${
            active === "demo" ? "bg-coral-500 text-white" : "text-coral-600 hover:bg-coral-100"
          }`}
        >
          <FlaskConical size={16} />
          Demo Lab
        </button>
        <p className="mt-1 px-2 text-xs text-coral-600/80">Safe local simulator</p>
      </div>
    </nav>
  );
}

interface MerchantSidebarProps {
  active: MerchantSection;
  onSelect: (section: MerchantSection) => void;
  mobileOpen: boolean;
  onCloseMobile: () => void;
}

export function MerchantSidebar({ active, onSelect, mobileOpen, onCloseMobile }: MerchantSidebarProps) {
  return (
    <>
      <aside className="sticky top-16 hidden h-[calc(100vh-4rem)] w-60 shrink-0 flex-col border-r border-sky-100 bg-white lg:flex">
        <NavList active={active} onSelect={onSelect} />
      </aside>

      <Overlay open={mobileOpen} onClose={onCloseMobile}>
        <motion.div
          role="dialog"
          aria-modal="true"
          aria-label="Navigation"
          initial={{ x: "-100%" }}
          animate={{ x: 0 }}
          exit={{ x: "-100%" }}
          transition={{ type: "spring", stiffness: 320, damping: 32 }}
          className="fixed inset-y-0 left-0 z-50 flex w-72 max-w-[85vw] flex-col bg-white shadow-float"
        >
          <div className="flex items-center justify-between border-b border-sky-100 px-4 py-3">
            <span className="text-sm font-semibold text-navy-900">Navigation</span>
            <button
              type="button"
              onClick={onCloseMobile}
              aria-label="Close navigation"
              className="flex h-8 w-8 items-center justify-center rounded-full text-navy-500 hover:bg-sky-50"
            >
              <X size={16} />
            </button>
          </div>
          <NavList
            active={active}
            onSelect={(section) => {
              onSelect(section);
              onCloseMobile();
            }}
          />
        </motion.div>
      </Overlay>
    </>
  );
}
