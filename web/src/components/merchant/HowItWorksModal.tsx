import { motion } from "framer-motion";
import { CheckCircle2, X } from "lucide-react";
import { Overlay } from "../Overlay";

interface HowItWorksModalProps {
  open: boolean;
  onClose: () => void;
}

const STEPS = [
  "Base booking is completed.",
  "ACTL may offer an optional upgrade.",
  "Buyer explicitly accepts or declines.",
  "A separate authorization/mandate is required.",
  "Existing policy, money-gate, payment, ledger, and audit safeguards run.",
  "No upgrade is charged automatically.",
];

export function HowItWorksModal({ open, onClose }: HowItWorksModalProps) {
  return (
    <Overlay open={open} onClose={onClose}>
      <motion.div
        role="dialog"
        aria-modal="true"
        aria-label="How upsells work"
        initial={{ opacity: 0, scale: 0.96, y: 12 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.96, y: 12 }}
        transition={{ type: "spring", stiffness: 320, damping: 28 }}
        className="fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-3xl bg-white p-6 shadow-float"
      >
        <div className="flex items-start justify-between">
          <h2 className="text-lg font-semibold text-navy-900">How consented upsells work</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-navy-500 hover:bg-sky-50"
          >
            <X size={16} />
          </button>
        </div>

        <ol className="mt-5 space-y-3">
          {STEPS.map((step, i) => (
            <li key={step} className="flex gap-3">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-sky-100 text-xs font-semibold text-navy-700">
                {i + 1}
              </span>
              <p className="text-sm text-navy-700">{step}</p>
            </li>
          ))}
        </ol>

        <div className="mt-5 flex items-center gap-2 rounded-xl bg-emerald-100 px-3 py-2.5 text-sm font-medium text-emerald-600">
          <CheckCircle2 size={16} />
          Upsells are separately approved. Never auto-charged.
        </div>
      </motion.div>
    </Overlay>
  );
}
