import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { MerchantHeader } from "../components/merchant/MerchantHeader";
import { MerchantSidebar } from "../components/merchant/MerchantSidebar";
import { CatalogSection } from "../components/merchant/CatalogSection";
import { DemoLabSection } from "../components/merchant/DemoLabSection";
import { GrowthSection } from "../components/merchant/GrowthSection";
import { LiveOrdersSection } from "../components/merchant/LiveOrdersSection";
import { OrderExplorer } from "../components/merchant/OrderExplorer";
import { OverviewSection } from "../components/merchant/OverviewSection";
import { TrustAuditSection } from "../components/merchant/TrustAuditSection";
import type { MerchantSection } from "./merchantSections";

export function MerchantPage() {
  const [searchParams] = useSearchParams();
  const deepLinkOrderId = searchParams.get("order_id");
  const deepLinkPanel = searchParams.get("panel");

  const [section, setSection] = useState<MerchantSection>(deepLinkOrderId ? "orders" : "overview");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [explorerOrderId, setExplorerOrderId] = useState<string | null>(deepLinkOrderId);

  // A buyer following a "View proof" link (?order_id=...&panel=proof)
  // should land straight on that order's evidence -- including a second
  // link for a different order while this page is already mounted (no
  // route change, so useState's initial value alone won't re-fire).
  // Adjusted during render, not an effect, mirroring QuoteDrawer's own
  // "reset when a prop changes" convention -- there's nothing external to
  // synchronize with, just local state that should follow the URL.
  const [lastDeepLinkOrderId, setLastDeepLinkOrderId] = useState(deepLinkOrderId);
  if (deepLinkOrderId !== lastDeepLinkOrderId) {
    setLastDeepLinkOrderId(deepLinkOrderId);
    if (deepLinkOrderId) {
      setExplorerOrderId(deepLinkOrderId);
      setSection("orders");
    }
  }

  return (
    <div className="min-h-screen bg-sky-50">
      <MerchantHeader onOpenSidebar={() => setSidebarOpen(true)} />
      <div className="mx-auto flex max-w-[1600px]">
        <MerchantSidebar
          active={section}
          onSelect={setSection}
          mobileOpen={sidebarOpen}
          onCloseMobile={() => setSidebarOpen(false)}
        />
        <main className="min-w-0 flex-1 px-4 py-6 sm:px-6 lg:px-8">
          {section === "overview" && <OverviewSection />}
          {section === "orders" && <LiveOrdersSection onOpenOrder={setExplorerOrderId} />}
          {section === "growth" && <GrowthSection />}
          {section === "catalog" && <CatalogSection />}
          {section === "trust" && <TrustAuditSection />}
          {section === "demo" && <DemoLabSection />}
        </main>
      </div>

      <OrderExplorer
        orderId={explorerOrderId}
        onClose={() => setExplorerOrderId(null)}
        showBackToBuyer={deepLinkPanel === "proof"}
      />
    </div>
  );
}
