import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, Lock, Minus, Send, ShieldCheck, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  useCatalog,
  useCreateMandate,
  useCreateQuote,
  useExtractMandate,
  useOrderStatus,
} from "../api/hooks";
import type { CatalogItem, ClarificationNeeded, MandateDraftReady } from "../api/types";
import { hotelDisplayName } from "../lib/hotelDisplay";
import { formatMinor } from "../lib/money";
import { useJourney } from "../state/journeyContext";
import { usePurchaseFlow } from "../state/usePurchaseFlow";
import { TrustCompassMark } from "./TrustCompassMark";

function defaultCheckIn(): string {
  const d = new Date();
  d.setDate(d.getDate() + 14);
  return d.toISOString().slice(0, 10);
}

interface PendingMandate {
  maxTotalMinor: number;
  nights: number;
  checkIn: string;
}

const QUICK_CHIPS: { label: string; text: string; apply: (f: { nights: number; guests: number; budgetMaxMinor: number; refundableOnly: boolean }) => Partial<{ nights: number; guests: number; budgetMaxMinor: number; refundableOnly: boolean }> }[] = [
  { label: "₹20–30k", text: "My budget is ₹20,000 to ₹30,000.", apply: () => ({ budgetMaxMinor: 3_000_000 }) },
  { label: "2 nights", text: "I need it for 2 nights.", apply: () => ({ nights: 2 }) },
  { label: "Refundable only", text: "It should be refundable.", apply: () => ({ refundableOnly: true }) },
  { label: "2 guests", text: "It's for 2 guests.", apply: () => ({ guests: 2 }) },
];

export function ChatPanel() {
  const {
    chatOpen,
    setChatOpen,
    chatMinimized,
    setChatMinimized,
    messages,
    addMessage,
    filters,
    setFilters,
    mandate,
    setMandate,
    activeOrder,
    setProofOrderId,
    quote,
    selectedSku,
  } = useJourney();

  const extractMandate = useExtractMandate();
  const createMandate = useCreateMandate();
  const createQuote = useCreateQuote();
  const { purchase, isPending: purchasePending } = usePurchaseFlow();
  const orderStatus = useOrderStatus(activeOrder?.orderId ?? null);
  const catalog = useCatalog(null);

  const [inputText, setInputText] = useState("");
  const [pending, setPending] = useState<PendingMandate | null>(null);
  const [clarification, setClarification] = useState<ClarificationNeeded | null>(null);
  const [showStructuredForm, setShowStructuredForm] = useState(false);
  const [formCheckIn, setFormCheckIn] = useState(defaultCheckIn);
  const [upsellDecision, setUpsellDecision] = useState<"idle" | "accepted" | "declined">("idle");
  const [upsellPending, setUpsellPending] = useState(false);
  const [upsellResultMsg, setUpsellResultMsg] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const greeted = useRef(false);

  useEffect(() => {
    if (chatOpen && !chatMinimized && messages.length === 0 && !greeted.current) {
      greeted.current = true;
      addMessage(
        "assistant",
        "Hi! I'm your ACTL travel assistant. Tell me what you're looking for in Goa and I'll help you book it safely.",
      );
    }
  }, [chatOpen, chatMinimized, messages.length, addMessage]);

  useEffect(() => {
    if (!chatOpen || chatMinimized) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setChatOpen(false);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [chatOpen, chatMinimized, setChatOpen]);

  useEffect(() => {
    if (chatOpen && !chatMinimized) inputRef.current?.focus();
  }, [chatOpen, chatMinimized]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, clarification, pending, mandate, activeOrder, upsellDecision]);

  const upsellCandidate = useMemo<CatalogItem | null>(() => {
    if (!catalog.data) return null;
    return (
      catalog.data.items.find((i) => i.sku !== quote?.sku && i.available_units > 0) ?? null
    );
  }, [catalog.data, quote]);

  async function runExtraction(nextUserText: string) {
    addMessage("user", nextUserText);
    setInputText("");
    const transcript = [...messages, { role: "user" as const, text: nextUserText, id: "", ts: 0 }]
      .filter((m) => m.role === "user")
      .map((m) => m.text)
      .join("\n");

    const result = await extractMandate.mutateAsync(transcript);
    if (result.status === "clarification_needed") {
      setClarification(result);
      setPending(null);
      addMessage(
        "assistant",
        result.questions.length > 0
          ? result.questions.join(" ")
          : "I still need a couple more details before I can lock a mandate.",
      );
    } else {
      const ready = result as MandateDraftReady;
      setClarification(null);
      setPending({
        maxTotalMinor: ready.max_total_minor,
        nights: ready.slots.nights ?? filters.nights,
        checkIn: ready.slots.check_in ?? defaultCheckIn(),
      });
      addMessage("assistant", "Got it — here's what I'll lock in. Review and confirm below.");
    }
  }

  async function handleSend() {
    const text = inputText.trim();
    if (!text) return;

    if (mandate && activeOrder) {
      addMessage("user", text);
      setInputText("");
      addMessage("assistant", "You can view your receipt or verify the audit proof above any time.");
      return;
    }
    if (mandate) {
      addMessage("user", text);
      setInputText("");
      addMessage("assistant", "Your mandate is locked — pick a stay from the list and tap Continue when ready.");
      return;
    }
    await runExtraction(text);
  }

  async function handleChip(chip: (typeof QUICK_CHIPS)[number]) {
    setFilters((f) => ({ ...f, ...chip.apply(f) }));
    if (!mandate) await runExtraction(chip.text);
  }

  function handleStructuredSubmit(e: React.FormEvent) {
    e.preventDefault();
    setClarification(null);
    setPending({ maxTotalMinor: filters.budgetMaxMinor, nights: filters.nights, checkIn: formCheckIn });
    setShowStructuredForm(false);
    addMessage("assistant", "Thanks — here's what I'll lock in. Review and confirm below.");
  }

  async function handleLockMandate() {
    if (!pending) return;
    const created = await createMandate.mutateAsync({
      nights: pending.nights,
      rooms: filters.guests,
      max_total_minor: pending.maxTotalMinor,
      require_refundable: filters.refundableOnly,
      check_in: pending.checkIn,
    });
    setMandate(created);
    setPending(null);
    addMessage(
      "assistant",
      `Your mandate is locked. Budget ${formatMinor(created.bounds.max_total_minor)}, max ${formatMinor(
        created.bounds.max_unit_minor,
      )}/night. Browse stays and tap Continue when you're ready.`,
    );
  }

  async function handleUpsellAccept() {
    if (!upsellCandidate) return;
    setUpsellPending(true);
    try {
      const upsellMandate = await createMandate.mutateAsync({
        nights: 1,
        rooms: 1,
        max_total_minor: upsellCandidate.unit_price_minor,
        require_refundable: false,
        check_in: defaultCheckIn(),
      });
      const upsellQuote = await createQuote.mutateAsync({
        sku: upsellCandidate.sku,
        mandate_id: upsellMandate.mandate_id,
        nights: 1,
      });
      const outcome = await purchase({
        quoteId: upsellQuote.quote_id,
        mandateId: upsellMandate.mandate_id,
        sku: upsellCandidate.sku,
        hotelName: hotelDisplayName(upsellCandidate.sku),
        totalMinor: upsellQuote.total_minor,
      });
      setUpsellDecision("accepted");
      setUpsellResultMsg(
        outcome.ok
          ? `Added! ${hotelDisplayName(upsellCandidate.sku)} for ${formatMinor(upsellQuote.total_minor)}, settled separately under its own mandate.`
          : `The add-on couldn't be completed (${outcome.reasonCode ?? "declined"}) — your original booking is unaffected.`,
      );
    } finally {
      setUpsellPending(false);
    }
  }

  function handleUpsellDecline() {
    setUpsellDecision("declined");
  }

  if (!chatOpen) return null;

  // The sticky trip bar occupies the bottom-right corner once a hotel is
  // selected -- shift the panel up on tablet/desktop (where it's a
  // corner widget, not a full sheet) so its own footer never sits on top
  // of the trip bar's Continue button.
  const tripBarClear = selectedSku !== null;

  return (
    <AnimatePresence>
      <motion.div
        role="complementary"
        aria-label="ACTL travel assistant"
        initial={{ opacity: 0, x: 40, scale: 0.98 }}
        animate={{ opacity: 1, x: 0, scale: 1 }}
        exit={{ opacity: 0, x: 40, scale: 0.98 }}
        transition={{ type: "spring", stiffness: 320, damping: 30 }}
        className={`fixed z-40 flex flex-col overflow-hidden rounded-3xl border border-sky-100 bg-white shadow-float transition-[height] ${
          chatMinimized
            ? `bottom-5 right-5 h-16 w-72 ${tripBarClear ? "sm:bottom-24" : "sm:bottom-6"} sm:right-6`
            : `inset-x-4 bottom-4 top-20 sm:inset-auto sm:right-6 sm:top-auto sm:h-[600px] sm:w-[380px] ${tripBarClear ? "sm:bottom-24" : "sm:bottom-6"}`
        }`}
      >
        <div className="flex shrink-0 items-center gap-3 border-b border-sky-100 px-4 py-3">
          <TrustCompassMark size={32} />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-navy-900">ACTL Travel Assistant</p>
            {!chatMinimized && (
              <span className="flex items-center gap-1 text-xs font-medium text-emerald-600">
                <ShieldCheck size={12} /> Safe booking mode
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={() => setChatMinimized(!chatMinimized)}
            aria-label={chatMinimized ? "Expand assistant" : "Minimize assistant"}
            className="flex h-8 w-8 items-center justify-center rounded-full text-navy-500 hover:bg-sky-50"
          >
            <Minus size={16} />
          </button>
          <button
            type="button"
            onClick={() => setChatOpen(false)}
            aria-label="Close assistant"
            className="flex h-8 w-8 items-center justify-center rounded-full text-navy-500 hover:bg-sky-50"
          >
            <X size={16} />
          </button>
        </div>

        {!chatMinimized && (
          <>
            <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
              {messages.map((m) => (
                <div
                  key={m.id}
                  className={`max-w-[85%] rounded-2xl px-3.5 py-2.5 text-sm ${
                    m.role === "user"
                      ? "ml-auto bg-ocean-600 text-white"
                      : "bg-sky-50 text-navy-900"
                  }`}
                >
                  {m.text}
                </div>
              ))}

              {extractMandate.isPending && (
                <div className="max-w-[70%] rounded-2xl bg-sky-50 px-3.5 py-2.5 text-sm text-navy-500">
                  Thinking…
                </div>
              )}

              {clarification && !pending && (
                <div className="rounded-2xl border border-sky-100 bg-white p-3 shadow-card">
                  <p className="mb-2 text-xs font-medium text-navy-500">
                    Or fill in the details directly:
                  </p>
                  {!showStructuredForm ? (
                    <button
                      type="button"
                      onClick={() => setShowStructuredForm(true)}
                      className="w-full rounded-xl border border-sky-100 px-3 py-2 text-sm font-medium text-navy-700 hover:bg-sky-50"
                    >
                      Fill mandate details
                    </button>
                  ) : (
                    <form onSubmit={handleStructuredSubmit} className="space-y-2.5">
                      <label className="block text-xs text-navy-500">
                        Total budget
                        <input
                          type="number"
                          min={1000}
                          step={500}
                          required
                          value={filters.budgetMaxMinor / 100}
                          onChange={(e) =>
                            setFilters((f) => ({ ...f, budgetMaxMinor: Number(e.target.value) * 100 }))
                          }
                          className="mt-1 w-full rounded-lg border border-sky-100 px-2.5 py-1.5 text-sm text-navy-900"
                        />
                      </label>
                      <label className="block text-xs text-navy-500">
                        Nights
                        <input
                          type="number"
                          min={1}
                          max={14}
                          required
                          value={filters.nights}
                          onChange={(e) => setFilters((f) => ({ ...f, nights: Number(e.target.value) }))}
                          className="mt-1 w-full rounded-lg border border-sky-100 px-2.5 py-1.5 text-sm text-navy-900"
                        />
                      </label>
                      <label className="block text-xs text-navy-500">
                        Check-in
                        <input
                          type="date"
                          required
                          value={formCheckIn}
                          onChange={(e) => setFormCheckIn(e.target.value)}
                          className="mt-1 w-full rounded-lg border border-sky-100 px-2.5 py-1.5 text-sm text-navy-900"
                        />
                      </label>
                      <button
                        type="submit"
                        className="w-full rounded-xl bg-ocean-600 px-3 py-2 text-sm font-semibold text-white hover:bg-ocean-500"
                      >
                        Use these details
                      </button>
                    </form>
                  )}
                </div>
              )}

              {pending && !mandate && (
                <div className="rounded-2xl border border-sky-100 bg-white p-4 shadow-card">
                  <p className="mb-3 text-sm font-semibold text-navy-900">Mandate review</p>
                  <dl className="space-y-1.5 text-sm">
                    <div className="flex justify-between">
                      <dt className="text-navy-500">Destination</dt>
                      <dd className="font-medium text-navy-900">Goa</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-navy-500">Nights</dt>
                      <dd className="font-medium text-navy-900">{pending.nights}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-navy-500">Budget cap</dt>
                      <dd className="font-medium text-navy-900">{formatMinor(pending.maxTotalMinor)}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-navy-500">Max/night</dt>
                      <dd className="font-medium text-navy-900">
                        {formatMinor(Math.floor(pending.maxTotalMinor / pending.nights))}
                      </dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-navy-500">Refundable</dt>
                      <dd className="font-medium text-navy-900">
                        {filters.refundableOnly ? "Required" : "Not required"}
                      </dd>
                    </div>
                  </dl>
                  <button
                    type="button"
                    onClick={handleLockMandate}
                    disabled={createMandate.isPending}
                    className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl bg-coral-500 px-3 py-2.5 text-sm font-semibold text-white hover:bg-coral-600 disabled:opacity-60"
                  >
                    <Lock size={15} />
                    {createMandate.isPending ? "Locking…" : "Lock & sign mandate"}
                  </button>
                </div>
              )}

              {activeOrder && orderStatus.data && (
                <div className="rounded-2xl border border-emerald-100 bg-emerald-100/40 p-4">
                  <p className="flex items-center gap-1.5 text-sm font-semibold text-emerald-600">
                    <CheckCircle2 size={16} /> Booking {orderStatus.data.status}
                  </p>
                  <p className="mt-1 text-sm text-navy-700">
                    {formatMinor(orderStatus.data.amount_minor)} · order {orderStatus.data.order_id}
                  </p>
                  <div className="mt-3 flex gap-2">
                    <button
                      type="button"
                      onClick={() => setProofOrderId(orderStatus.data.order_id)}
                      className="rounded-full border border-sky-100 bg-white px-3 py-1.5 text-xs font-medium text-navy-700 hover:bg-sky-50"
                    >
                      Verify proof
                    </button>
                  </div>
                </div>
              )}

              {activeOrder &&
                orderStatus.data?.status === "CAPTURED" &&
                upsellCandidate &&
                upsellDecision === "idle" && (
                  <div className="rounded-2xl border border-coral-100 bg-white p-4 shadow-card">
                    <p className="text-sm font-semibold text-navy-900">
                      Add {hotelDisplayName(upsellCandidate.sku)} for one more night?
                    </p>
                    <p className="mt-1 text-xs text-navy-500">
                      {formatMinor(upsellCandidate.unit_price_minor)} · requires separate approval —
                      never charged automatically.
                    </p>
                    <div className="mt-3 flex gap-2">
                      <button
                        type="button"
                        onClick={handleUpsellAccept}
                        disabled={upsellPending || purchasePending}
                        className="flex-1 rounded-xl bg-coral-500 px-3 py-2 text-sm font-semibold text-white hover:bg-coral-600 disabled:opacity-60"
                      >
                        {upsellPending ? "Adding…" : "Accept"}
                      </button>
                      <button
                        type="button"
                        onClick={handleUpsellDecline}
                        className="flex-1 rounded-xl border border-sky-100 px-3 py-2 text-sm font-medium text-navy-700 hover:bg-sky-50"
                      >
                        Decline
                      </button>
                    </div>
                  </div>
                )}

              {upsellDecision === "accepted" && upsellResultMsg && (
                <div className="rounded-2xl bg-sky-50 px-3.5 py-2.5 text-sm text-navy-900">
                  {upsellResultMsg}
                </div>
              )}
              {upsellDecision === "declined" && (
                <div className="rounded-2xl bg-sky-50 px-3.5 py-2.5 text-sm text-navy-900">
                  No problem — nothing extra was charged.
                </div>
              )}
            </div>

            {!mandate && (
              <div className="flex shrink-0 flex-wrap gap-1.5 border-t border-sky-100 px-4 py-2.5">
                {QUICK_CHIPS.map((chip) => (
                  <button
                    key={chip.label}
                    type="button"
                    onClick={() => handleChip(chip)}
                    className="rounded-full bg-sky-50 px-3 py-1.5 text-xs font-medium text-navy-700 hover:bg-sky-100"
                  >
                    {chip.label}
                  </button>
                ))}
              </div>
            )}

            <form
              onSubmit={(e) => {
                e.preventDefault();
                void handleSend();
              }}
              className="flex shrink-0 items-center gap-2 border-t border-sky-100 p-3"
            >
              <label className="sr-only" htmlFor="chat-input">
                Message ACTL assistant
              </label>
              <input
                id="chat-input"
                ref={inputRef}
                type="text"
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                placeholder="Book me something nice in Goa…"
                className="flex-1 rounded-full border border-sky-100 bg-sky-50 px-4 py-2.5 text-sm text-navy-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ocean-500"
              />
              <button
                type="submit"
                aria-label="Send message"
                disabled={!inputText.trim()}
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-ocean-600 text-white hover:bg-ocean-500 disabled:opacity-50"
              >
                <Send size={16} />
              </button>
            </form>
          </>
        )}
      </motion.div>
    </AnimatePresence>
  );
}
