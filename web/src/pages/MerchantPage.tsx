import { useState } from "react";
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
  const [section, setSection] = useState<MerchantSection>("overview");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [explorerOrderId, setExplorerOrderId] = useState<string | null>(null);

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

      <OrderExplorer orderId={explorerOrderId} onClose={() => setExplorerOrderId(null)} />
    </div>
  );
}
