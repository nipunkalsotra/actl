import { lazy, Suspense } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { BuyerPage } from "./pages/BuyerPage";

// Lazy-loaded: a buyer-only visitor should never pay for the merchant
// dashboard's bundle weight (charting/table code neither page shares).
const MerchantPage = lazy(() =>
  import("./pages/MerchantPage").then((m) => ({ default: m.MerchantPage })),
);

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<BuyerPage />} />
        <Route
          path="/merchant"
          element={
            <Suspense fallback={<div className="min-h-screen bg-sky-50" />}>
              <MerchantPage />
            </Suspense>
          }
        />
      </Routes>
    </BrowserRouter>
  );
}
