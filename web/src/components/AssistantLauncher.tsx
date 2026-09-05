import { motion } from "framer-motion";
import { useJourney } from "../state/journeyContext";
import { TrustCompassMark } from "./TrustCompassMark";

export function AssistantLauncher() {
  const { setChatOpen, setChatMinimized, selectedSku } = useJourney();

  const handleOpen = () => {
    setChatOpen(true);
    setChatMinimized(false);
  };

  // The sticky trip bar (bottom-anchored, full-width) appears once a hotel
  // is selected -- both launcher variants shift up so neither ever sits on
  // top of that bar's own Continue/View quote buttons. `calc(...+env(...))`
  // keeps the launcher clear of a device's own home-indicator/notch safe
  // area, not just the app's own fixed controls.
  const mobileBottom = selectedSku
    ? "bottom-[calc(6rem+env(safe-area-inset-bottom))]"
    : "bottom-[calc(1.25rem+env(safe-area-inset-bottom))]";
  const desktopBottom = selectedSku ? "sm:bottom-24" : "sm:bottom-6";

  return (
    <>
      {/* Mobile: a compact circular FAB, bottom-LEFT -- never the wide
          chip's footprint, and never on the same side as a hotel card's
          own action buttons (View details / Select), which sit right-
          aligned per HotelCard's own items-end. This is what actually
          makes the overlap structurally impossible, not just less likely:
          web/tests/mobile.spec.ts asserts this with a real bounding-box/
          elementFromPoint check, not a scroll workaround. */}
      <motion.button
        type="button"
        initial={{ opacity: 0, scale: 0.8 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.8 }}
        transition={{ type: "spring", stiffness: 300, damping: 24 }}
        onClick={handleOpen}
        aria-label="Open ACTL travel assistant"
        className={`fixed left-[calc(1.25rem+env(safe-area-inset-left))] z-30 flex h-14 w-14 items-center justify-center rounded-full bg-card shadow-float hover:shadow-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-ocean-500 sm:hidden ${mobileBottom}`}
      >
        <span className="relative shrink-0">
          <TrustCompassMark size={32} />
          <span className="absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-card bg-emerald-500" />
        </span>
      </motion.button>

      {/* Tablet/desktop: unchanged wide chip, bottom-right. */}
      <motion.button
        type="button"
        initial={{ opacity: 0, scale: 0.8 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.8 }}
        transition={{ type: "spring", stiffness: 300, damping: 24 }}
        onClick={handleOpen}
        aria-label="Open ACTL travel assistant"
        className={`fixed right-5 z-30 hidden items-center gap-3 rounded-full bg-card py-2 pl-2 pr-4 shadow-float hover:shadow-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-ocean-500 sm:right-6 sm:flex ${desktopBottom}`}
      >
        <span className="relative shrink-0">
          <TrustCompassMark size={44} />
          <span className="absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full border-2 border-card bg-emerald-500" />
        </span>
        <span className="text-left leading-tight">
          <span className="block text-sm font-semibold text-navy-900">Planning a Goa escape?</span>
          <span className="block text-xs text-navy-500">Tell me what you need</span>
        </span>
      </motion.button>
    </>
  );
}
