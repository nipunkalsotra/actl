import { AnimatePresence } from "framer-motion";
import { useState } from "react";
import { AssistantLauncher } from "../components/AssistantLauncher";
import { CatalogGrid } from "../components/CatalogGrid";
import { ChatPanel } from "../components/ChatPanel";
import { FiltersCard } from "../components/FiltersCard";
import { Header } from "../components/Header";
import { HelpModal } from "../components/HelpModal";
import { HotelDetailsDrawer } from "../components/HotelDetailsDrawer";
import { MandateCard } from "../components/MandateCard";
import { MobileFiltersSheet } from "../components/MobileFiltersSheet";
import { MyTripsDrawer } from "../components/MyTripsDrawer";
import { QuoteDrawer } from "../components/QuoteDrawer";
import { StickyTripBar } from "../components/StickyTripBar";
import { TrustStrip } from "../components/TrustStrip";
import { useMediaQuery } from "../lib/useMediaQuery";
import { JourneyProvider } from "../state/journey";
import { useJourney } from "../state/journeyContext";

function BuyerShell() {
  const { chatOpen, setChatOpen } = useJourney();
  const [tripsOpen, setTripsOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  // Single source of truth for which FiltersCard is mounted -- never both
  // at once (a CSS-hidden duplicate would still be a real DOM node,
  // which is both wasted work and ambiguous for anything that queries
  // the page by accessible role/label, tests included).
  const isDesktop = useMediaQuery("(min-width: 1024px)");

  return (
    <div className="min-h-screen bg-sky-50">
      <Header onOpenTrips={() => setTripsOpen(true)} onOpenHelp={() => setHelpOpen(true)} />
      <TrustStrip />

      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[260px_1fr]">
          <aside className="space-y-4 lg:sticky lg:top-24 lg:self-start">
            {isDesktop && <FiltersCard />}
            <MandateCard onOpenChat={() => setChatOpen(true)} />
          </aside>
          <div className="min-w-0">
            {!isDesktop && <MobileFiltersSheet />}
            <CatalogGrid />
          </div>
        </div>
      </main>

      <StickyTripBar />

      <AnimatePresence>{!chatOpen && <AssistantLauncher />}</AnimatePresence>
      <ChatPanel />

      <HotelDetailsDrawer />
      <QuoteDrawer />
      <MyTripsDrawer open={tripsOpen} onClose={() => setTripsOpen(false)} />
      <HelpModal open={helpOpen} onClose={() => setHelpOpen(false)} />
    </div>
  );
}

export function BuyerPage() {
  return (
    <JourneyProvider>
      <BuyerShell />
    </JourneyProvider>
  );
}
