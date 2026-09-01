import { Briefcase, CircleHelp, LayoutDashboard, RotateCcw, User } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useConfig } from "../api/hooks";

interface ProfileMenuProps {
  onOpenTrips: () => void;
  onOpenHelp: () => void;
}

export function ProfileMenu({ onOpenTrips, onOpenHelp }: ProfileMenuProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const config = useConfig();

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    const onClickAway = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("mousedown", onClickAway);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("mousedown", onClickAway);
    };
  }, [open]);

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex h-9 w-9 items-center justify-center rounded-full bg-sky-100 text-navy-700 hover:bg-sky-100/80 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ocean-500"
      >
        <User size={18} />
        <span className="sr-only">Open profile menu</span>
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 z-40 mt-2 w-64 rounded-2xl border border-sky-100 bg-white p-2 shadow-card"
        >
          <div className="px-3 py-2">
            <p className="text-sm font-semibold text-navy-900">Demo buyer</p>
            <p className="text-xs text-navy-500">
              Payments: {config.data?.payment_provider ?? "…"} (test mode) · {config.data?.currency ?? "INR"}
            </p>
          </div>
          <div className="my-1 h-px bg-sky-100" />
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onOpenTrips();
            }}
            className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-navy-700 hover:bg-sky-50 sm:hidden"
          >
            <Briefcase size={16} /> My trips
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onOpenHelp();
            }}
            className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-navy-700 hover:bg-sky-50 sm:hidden"
          >
            <CircleHelp size={16} /> Help
          </button>
          <Link
            to="/merchant"
            role="menuitem"
            onClick={() => setOpen(false)}
            className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-navy-700 hover:bg-sky-50 sm:hidden"
          >
            <LayoutDashboard size={16} /> Merchant view
          </Link>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              localStorage.removeItem("actl.trips.v1");
              window.location.reload();
            }}
            className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-navy-700 hover:bg-sky-50"
          >
            <RotateCcw size={16} /> Reset demo session
          </button>
        </div>
      )}
    </div>
  );
}
