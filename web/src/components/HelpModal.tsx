import { motion } from "framer-motion";
import { CircleHelp, ShieldCheck, X } from "lucide-react";
import { Overlay } from "./Overlay";

interface HelpModalProps {
  open: boolean;
  onClose: () => void;
}

const POINTS = [
  {
    title: "Your budget is a hard cap",
    body: "Every purchase is checked against a signed spending mandate before any charge. ACTL can't spend more than you authorized, on anything outside your allowed categories.",
  },
  {
    title: "Nothing charges without a real, verified quote",
    body: "Prices are pinned and expire on a timer. A stale or tampered price is rejected before checkout, never silently accepted.",
  },
  {
    title: "Every action is logged, tamper-evident",
    body: "Mandate, quote, payment and settlement steps are recorded in an append-only, hash-chained audit log you can verify yourself from the receipt.",
  },
  {
    title: "This is a demo, test-mode environment",
    body: "Inventory shown here is demo partner inventory. Payments run through a deterministic simulator (or Razorpay Test Mode) — no real money ever moves.",
  },
];

export function HelpModal({ open, onClose }: HelpModalProps) {
  return (
    <Overlay open={open} onClose={onClose}>
      <motion.div
        role="dialog"
        aria-modal="true"
        aria-label="How ACTL protects your booking"
        initial={{ opacity: 0, scale: 0.96, y: 12 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.96, y: 12 }}
        transition={{ type: "spring", stiffness: 320, damping: 28 }}
        className="fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-3xl bg-white p-6 shadow-float"
      >
        <div className="flex items-start justify-between">
          <h2 className="flex items-center gap-2 text-lg font-semibold text-navy-900">
            <CircleHelp size={20} className="text-ocean-600" />
            How ACTL protects your booking
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close help"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-navy-500 hover:bg-sky-50"
          >
            <X size={16} />
          </button>
        </div>

        <ul className="mt-5 space-y-4">
          {POINTS.map((point) => (
            <li key={point.title} className="flex gap-3">
              <ShieldCheck size={18} className="mt-0.5 shrink-0 text-emerald-600" />
              <div>
                <p className="text-sm font-semibold text-navy-900">{point.title}</p>
                <p className="mt-0.5 text-sm text-navy-500">{point.body}</p>
              </div>
            </li>
          ))}
        </ul>
      </motion.div>
    </Overlay>
  );
}
