import { motion } from "framer-motion";
import { useJourney } from "../state/journeyContext";
import { TrustCompassMark } from "./TrustCompassMark";

export function AssistantLauncher() {
  const { setChatOpen, setChatMinimized, selectedSku } = useJourney();
  // The sticky trip bar (bottom-anchored, full-width) appears once a
  // hotel is selected -- shift the launcher up on tablet/desktop so it
  // never sits on top of that bar's own Continue/View quote buttons.
  const bottomClass = selectedSku ? "bottom-5 sm:bottom-24" : "bottom-5 sm:bottom-6";

  return (
    <motion.button
      type="button"
      initial={{ opacity: 0, scale: 0.8 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.8 }}
      transition={{ type: "spring", stiffness: 300, damping: 24 }}
      onClick={() => {
        setChatOpen(true);
        setChatMinimized(false);
      }}
      className={`fixed right-5 z-30 flex items-center gap-3 rounded-full bg-card py-2 pl-2 pr-4 shadow-float hover:shadow-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-ocean-500 sm:right-6 ${bottomClass}`}
      aria-label="Open ACTL travel assistant"
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
  );
}
