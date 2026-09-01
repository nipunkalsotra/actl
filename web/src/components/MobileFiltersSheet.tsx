import { motion } from "framer-motion";
import { SlidersHorizontal, X } from "lucide-react";
import { useState } from "react";
import { FiltersCard } from "./FiltersCard";
import { Overlay } from "./Overlay";

export function MobileFiltersSheet() {
  const [open, setOpen] = useState(false);

  return (
    <div className="mb-4 lg:hidden">
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex items-center gap-2 rounded-full border border-sky-100 bg-white px-4 py-2.5 text-sm font-medium text-navy-700 shadow-card"
      >
        <SlidersHorizontal size={16} />
        Filters
      </button>

      <Overlay open={open} onClose={() => setOpen(false)}>
        <motion.div
          role="dialog"
          aria-modal="true"
          aria-label="Filters"
          initial={{ y: "100%" }}
          animate={{ y: 0 }}
          exit={{ y: "100%" }}
          transition={{ type: "spring", stiffness: 320, damping: 32 }}
          className="fixed inset-x-0 bottom-0 z-50 max-h-[85vh] overflow-y-auto rounded-t-3xl bg-sky-50 p-4 pb-8 shadow-float"
        >
          <div className="mb-2 flex items-center justify-between">
            <span className="mx-auto h-1.5 w-10 rounded-full bg-sky-100" />
            <button
              type="button"
              onClick={() => setOpen(false)}
              aria-label="Close filters"
              className="absolute right-4 top-4 flex h-8 w-8 items-center justify-center rounded-full bg-white text-navy-500 shadow-sm"
            >
              <X size={16} />
            </button>
          </div>
          <FiltersCard />
        </motion.div>
      </Overlay>
    </div>
  );
}
