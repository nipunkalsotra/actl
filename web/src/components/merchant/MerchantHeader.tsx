import { ExternalLink, Menu, User } from "lucide-react";
import { Link } from "react-router-dom";
import { TrustCompassMark } from "../TrustCompassMark";
import { SystemHealthPopover } from "./SystemHealthPopover";

const TODAY = new Date().toLocaleDateString("en-IN", {
  year: "numeric",
  month: "short",
  day: "numeric",
});

interface MerchantHeaderProps {
  onOpenSidebar: () => void;
}

export function MerchantHeader({ onOpenSidebar }: MerchantHeaderProps) {
  return (
    <header className="sticky top-0 z-30 border-b border-sky-100 bg-white">
      <div className="flex h-16 items-center gap-3 px-4 sm:gap-4 sm:px-6">
        <button
          type="button"
          onClick={onOpenSidebar}
          aria-label="Open navigation"
          className="flex h-9 w-9 items-center justify-center rounded-lg text-navy-700 hover:bg-sky-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ocean-500 lg:hidden"
        >
          <Menu size={20} />
        </button>

        <Link to="/merchant" className="flex shrink-0 items-center gap-2">
          <TrustCompassMark size={32} />
          <span className="hidden text-lg font-semibold tracking-tight text-navy-900 sm:inline">
            ACTL.
          </span>
        </Link>

        <div className="hidden h-6 w-px bg-sky-100 md:block" />

        <h1 className="hidden truncate text-base font-semibold text-navy-900 md:block">
          Merchant Control Center
        </h1>

        <span className="ml-2 hidden items-center gap-1.5 rounded-full border border-sky-100 bg-sky-50 px-3 py-1.5 text-xs font-medium text-navy-700 lg:flex">
          Demo run: {TODAY}
        </span>

        <div className="ml-auto flex items-center gap-2 sm:gap-3">
          <div className="hidden sm:block">
            <SystemHealthPopover />
          </div>
          <Link
            to="/"
            className="flex items-center gap-1.5 rounded-full border border-sky-100 px-3 py-2 text-sm font-medium text-navy-700 hover:bg-sky-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ocean-500"
          >
            Buyer experience
            <ExternalLink size={14} />
          </Link>
          <span className="flex h-9 w-9 items-center justify-center rounded-full bg-sky-100 text-navy-700">
            <User size={18} />
          </span>
        </div>
      </div>
      <div className="border-t border-sky-100 px-4 py-2 sm:hidden">
        <SystemHealthPopover />
      </div>
    </header>
  );
}
