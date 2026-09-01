import { createContext, useContext } from "react";
import type { MandateResponse, QuoteResponse } from "../api/types";

export interface Filters {
  nights: number;
  guests: number;
  budgetMaxMinor: number;
  refundableOnly: boolean;
  minRating: number;
}

export const DEFAULT_FILTERS: Filters = {
  nights: 2,
  guests: 2,
  budgetMaxMinor: 3_000_000, // ~30,000
  refundableOnly: true,
  minRating: 0,
};

export type SortMode = "best_match" | "price_asc" | "rating_desc";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  ts: number;
}

export interface TripRecord {
  orderId: string;
  sku: string;
  hotelName: string;
  totalMinor: number;
  status: string;
  createdAt: number;
}

export interface ActiveOrder {
  orderId: string;
  sagaId: string;
}

export interface JourneyValue {
  filters: Filters;
  setFilters: (updater: (prev: Filters) => Filters) => void;
  sortMode: SortMode;
  setSortMode: (mode: SortMode) => void;

  mandate: MandateResponse | null;
  setMandate: (mandate: MandateResponse | null) => void;

  selectedSku: string | null;
  setSelectedSku: (sku: string | null) => void;

  quote: QuoteResponse | null;
  setQuote: (quote: QuoteResponse | null) => void;

  activeOrder: ActiveOrder | null;
  setActiveOrder: (order: ActiveOrder | null) => void;

  chatOpen: boolean;
  setChatOpen: (open: boolean) => void;
  chatMinimized: boolean;
  setChatMinimized: (min: boolean) => void;
  messages: ChatMessage[];
  addMessage: (role: "user" | "assistant", text: string) => void;
  resetMessages: () => void;

  trips: TripRecord[];
  addTrip: (trip: TripRecord) => void;

  detailsSku: string | null;
  setDetailsSku: (sku: string | null) => void;

  quoteDrawerOpen: boolean;
  setQuoteDrawerOpen: (open: boolean) => void;

  proofOrderId: string | null;
  setProofOrderId: (orderId: string | null) => void;

  resetForNewBrowse: () => void;
}

export const JourneyContext = createContext<JourneyValue | null>(null);

export function useJourney(): JourneyValue {
  const ctx = useContext(JourneyContext);
  if (!ctx) throw new Error("useJourney must be used within JourneyProvider");
  return ctx;
}
