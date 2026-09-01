import { useEffect, useMemo, useState, type ReactNode } from "react";
import type { MandateResponse, QuoteResponse } from "../api/types";
import {
  DEFAULT_FILTERS,
  JourneyContext,
  type ActiveOrder,
  type ChatMessage,
  type Filters,
  type JourneyValue,
  type SortMode,
  type TripRecord,
} from "./journeyContext";

const TRIPS_STORAGE_KEY = "actl.trips.v1";

function loadTrips(): TripRecord[] {
  try {
    const raw = localStorage.getItem(TRIPS_STORAGE_KEY);
    return raw ? (JSON.parse(raw) as TripRecord[]) : [];
  } catch {
    return [];
  }
}

export function JourneyProvider({ children }: { children: ReactNode }) {
  const [filters, setFiltersState] = useState<Filters>(DEFAULT_FILTERS);
  const [sortMode, setSortMode] = useState<SortMode>("best_match");
  const [mandate, setMandate] = useState<MandateResponse | null>(null);
  const [selectedSku, setSelectedSku] = useState<string | null>(null);
  const [quote, setQuote] = useState<QuoteResponse | null>(null);
  const [activeOrder, setActiveOrder] = useState<ActiveOrder | null>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const [chatMinimized, setChatMinimized] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [trips, setTrips] = useState<TripRecord[]>(() => loadTrips());
  const [detailsSku, setDetailsSku] = useState<string | null>(null);
  const [quoteDrawerOpen, setQuoteDrawerOpen] = useState(false);
  const [proofOrderId, setProofOrderId] = useState<string | null>(null);

  useEffect(() => {
    localStorage.setItem(TRIPS_STORAGE_KEY, JSON.stringify(trips));
  }, [trips]);

  const setFilters = (updater: (prev: Filters) => Filters) => setFiltersState(updater);

  const addMessage = (role: "user" | "assistant", text: string) =>
    setMessages((prev) => [...prev, { id: `${Date.now()}-${prev.length}`, role, text, ts: Date.now() }]);

  const resetMessages = () => setMessages([]);

  const addTrip = (trip: TripRecord) =>
    setTrips((prev) => [trip, ...prev.filter((t) => t.orderId !== trip.orderId)].slice(0, 20));

  const resetForNewBrowse = () => {
    setSelectedSku(null);
    setQuote(null);
    setActiveOrder(null);
    setDetailsSku(null);
    setQuoteDrawerOpen(false);
    setProofOrderId(null);
  };

  const value = useMemo<JourneyValue>(
    () => ({
      filters,
      setFilters,
      sortMode,
      setSortMode,
      mandate,
      setMandate,
      selectedSku,
      setSelectedSku,
      quote,
      setQuote,
      activeOrder,
      setActiveOrder,
      chatOpen,
      setChatOpen,
      chatMinimized,
      setChatMinimized,
      messages,
      addMessage,
      resetMessages,
      trips,
      addTrip,
      detailsSku,
      setDetailsSku,
      quoteDrawerOpen,
      setQuoteDrawerOpen,
      proofOrderId,
      setProofOrderId,
      resetForNewBrowse,
    }),
    [
      filters,
      sortMode,
      mandate,
      selectedSku,
      quote,
      activeOrder,
      chatOpen,
      chatMinimized,
      messages,
      trips,
      detailsSku,
      quoteDrawerOpen,
      proofOrderId,
    ],
  );

  return <JourneyContext.Provider value={value}>{children}</JourneyContext.Provider>;
}
