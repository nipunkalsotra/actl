import { AnimatePresence, motion } from "framer-motion";
import { useEffect, type ReactNode } from "react";

interface OverlayProps {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
}

/** Shared backdrop + Escape-to-close behind every drawer/modal in this app
 * -- each caller supplies its own positioned panel (centered, side-slide,
 * or bottom-sheet) as children; only the parts that were identical across
 * all six of them live here. */
export function Overlay({ open, onClose, children }: OverlayProps) {
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-40 bg-navy-900/40"
            onClick={onClose}
          />
          {children}
        </>
      )}
    </AnimatePresence>
  );
}
