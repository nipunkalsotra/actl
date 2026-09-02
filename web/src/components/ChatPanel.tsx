import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, Lock, Minus, RotateCcw, Send, ShieldCheck, Sparkles, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  useConfig,
  useCreateMandate,
  useDeclineUpsell,
  useExtractMandate,
  useOrderStatus,
  usePurchaseUpsell,
  useUpsellOffers,
} from "../api/hooks";
import type {
  ClarificationNeeded,
  KnownMandateSlots,
  MandateDraftReady,
  UpsellOffer,
} from "../api/types";
import { formatMinor } from "../lib/money";
import { useJourney } from "../state/journeyContext";
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

const EMPTY_SLOTS: KnownMandateSlots = {
  category: null,
  location: null,
  check_in: null,
  nights: null,
  rooms: null,
  currency: null,
  guests: null,
  refundable: null,
};

// Merge, never overwrite-with-nothing: a turn that didn't unambiguously
// re-state a field returns null for it, and that must never clear a
// value a previous turn already confirmed.
function mergeSlots(prev: KnownMandateSlots, next: KnownMandateSlots): KnownMandateSlots {
  return {
    category: next.category ?? prev.category,
    location: next.location ?? prev.location,
    check_in: next.check_in ?? prev.check_in,
    nights: next.nights ?? prev.nights,
    rooms: next.rooms ?? prev.rooms,
    currency: next.currency ?? prev.currency,
    guests: next.guests ?? prev.guests,
    refundable: next.refundable ?? prev.refundable,
  };
}

// Plain, templated acknowledgement of what's already understood -- never
// phrased as "AI understood," since this runs identically whether Groq or
// the deterministic fallback produced the slots.
function describeKnownSlots(slots: KnownMandateSlots, maxTotalMinor: number | null): string | null {
  const parts: string[] = [];
  if (slots.category === "travel.hotel") parts.push("a hotel stay");
  if (slots.location) parts.push(`in ${slots.location}`);
  if (slots.nights) parts.push(`for ${slots.nights} night${slots.nights === 1 ? "" : "s"}`);
  if (maxTotalMinor) parts.push(`up to ${formatMinor(maxTotalMinor)} total`);
  if (slots.check_in) parts.push(`checking in ${slots.check_in}`);
  if (slots.rooms) parts.push(`${slots.rooms} room${slots.rooms === 1 ? "" : "s"}`);
  if (slots.guests) parts.push(`${slots.guests} guest${slots.guests === 1 ? "" : "s"}`);
  if (slots.refundable === true) parts.push("refundable");
  if (slots.refundable === false) parts.push("non-refundable");
  return parts.length > 0 ? `Got it — ${parts.join(", ")}.` : null;
}

interface ChipDef {
  label: string;
  text: string;
  apply?: (f: { nights: number; guests: number; budgetMaxMinor: number; refundableOnly: boolean }) => Partial<{ nights: number; guests: number; budgetMaxMinor: number; refundableOnly: boolean }>;
}

// Chips for whichever single slot is currently being asked about -- never
// a static, always-visible list unrelated to the live question.
function chipsForSlot(slot: string | undefined): ChipDef[] {
  switch (slot) {
    case "max_total_minor":
      return [
        { label: "₹15,000", text: "My budget is ₹15,000.", apply: () => ({ budgetMaxMinor: 1_500_000 }) },
        { label: "₹20,000", text: "My budget is ₹20,000.", apply: () => ({ budgetMaxMinor: 2_000_000 }) },
        { label: "₹30,000", text: "My budget is ₹30,000.", apply: () => ({ budgetMaxMinor: 3_000_000 }) },
      ];
    case "nights":
      return [
        { label: "1 night", text: "1 night.", apply: () => ({ nights: 1 }) },
        { label: "2 nights", text: "2 nights.", apply: () => ({ nights: 2 }) },
        { label: "3 nights", text: "3 nights.", apply: () => ({ nights: 3 }) },
      ];
    case "rooms":
      return [
        { label: "1 room", text: "1 room." },
        { label: "2 rooms", text: "2 rooms." },
      ];
    case "category":
      return [{ label: "Hotel stay", text: "A hotel stay." }];
    case "location":
      return [{ label: "Goa", text: "In Goa." }];
    default:
      return [];
  }
}

// Mirrors backend `_QUESTION_PRIORITY` in domain/mandate/draft.py -- which
// single missing slot the one question currently in play is about, so
// chips/date-picker can target it. Kept in sync manually; both sides
// share the same fixed REQUIRED_SLOTS set.
const QUESTION_PRIORITY = ["max_total_minor", "location", "check_in", "nights", "rooms", "category", "currency"];

function currentQuestionSlot(missingSlots: string[]): string | undefined {
  return QUESTION_PRIORITY.find((s) => missingSlots.includes(s));
}

// Shown only before the first message, as conversation starters -- once a
// real clarification question is live, chipsForSlot() takes over instead.
const STARTER_CHIPS: ChipDef[] = [
  { label: "₹20–30k", text: "My budget is ₹20,000 to ₹30,000.", apply: () => ({ budgetMaxMinor: 3_000_000 }) },
  { label: "2 nights", text: "I need it for 2 nights.", apply: () => ({ nights: 2 }) },
  { label: "Refundable only", text: "It should be refundable.", apply: () => ({ refundableOnly: true }) },
  { label: "2 guests", text: "It's for 2 guests.", apply: () => ({ guests: 2 }) },
];

const TRIP_DETAIL_LABELS: { key: keyof KnownMandateSlots; format: (v: never) => string }[] = [
  { key: "category", format: (v: string) => (v === "travel.hotel" ? "Hotel" : v) },
  { key: "location", format: (v: string) => v },
  { key: "nights", format: (v: number) => `${v} night${v === 1 ? "" : "s"}` },
  { key: "check_in", format: (v: string) => v },
  { key: "rooms", format: (v: number) => `${v} room${v === 1 ? "" : "s"}` },
  { key: "guests", format: (v: number) => `${v} guest${v === 1 ? "" : "s"}` },
  { key: "refundable", format: (v: boolean) => (v ? "Refundable" : "Non-refundable") },
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
    selectedSku,
  } = useJourney();
  const navigate = useNavigate();

  const extractMandate = useExtractMandate();
  const createMandate = useCreateMandate();
  const config = useConfig();
  const orderStatus = useOrderStatus(activeOrder?.orderId ?? null);

  const [inputText, setInputText] = useState("");
  const [pending, setPending] = useState<PendingMandate | null>(null);
  const [clarification, setClarification] = useState<ClarificationNeeded | null>(null);
  // The structured partial mandate draft, accumulated across turns --
  // merged (never regressed by an ambiguous later turn) in runExtraction.
  const [knownSlots, setKnownSlots] = useState<KnownMandateSlots>(EMPTY_SLOTS);
  const [lastFailedText, setLastFailedText] = useState<string | null>(null);
  const [showStructuredForm, setShowStructuredForm] = useState(false);
  const [formCheckIn, setFormCheckIn] = useState(defaultCheckIn);

  // §28 P12 contextual upsell -- a real, explicit, separately-approved
  // flow. "idle": nothing shown yet (or dismissed). "options": the buyer
  // asked to see extras. "review": one offer selected, not yet approved
  // -- this is the only stage that can lead to a real purchase, and only
  // via its own explicit Approve click. "result": the purchase attempt
  // finished, success or honest failure.
  const [upsellStage, setUpsellStage] = useState<"idle" | "options" | "review" | "result">("idle");
  const [upsellDismissed, setUpsellDismissed] = useState(false);
  const [selectedOffer, setSelectedOffer] = useState<UpsellOffer | null>(null);
  const [upsellResult, setUpsellResult] = useState<{
    ok: boolean;
    offer: UpsellOffer;
    addonOrderId: string | null;
    reasonCode: string | null;
  } | null>(null);

  const upsellOffers = useUpsellOffers(
    activeOrder?.orderId ?? null,
    orderStatus.data?.status === "CAPTURED",
  );
  const purchaseUpsell = usePurchaseUpsell();
  const declineUpsell = useDeclineUpsell();

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
  }, [messages, clarification, pending, mandate, activeOrder, upsellStage, knownSlots]);

  // Any newly-understood refundable/guests preference should flow into the
  // same filters the review card and mandate-lock call already read from --
  // otherwise something the buyer said in chat ("refundable", "2 guests")
  // would be silently dropped at lock time.
  function syncFiltersFromSlots(slots: KnownMandateSlots) {
    setFilters((f) => ({
      ...f,
      ...(slots.refundable !== null ? { refundableOnly: slots.refundable } : {}),
      ...(slots.guests !== null ? { guests: slots.guests } : {}),
      ...(slots.nights !== null ? { nights: slots.nights } : {}),
    }));
  }

  // Shared by a fresh send and a retry -- retry must re-run the same
  // transcript without re-adding a duplicate user bubble for a message
  // that's already shown in the thread.
  async function attemptExtraction(transcript: string) {
    try {
      const result = await extractMandate.mutateAsync(transcript);
      if (result.status === "clarification_needed") {
        const merged = mergeSlots(knownSlots, result.slots);
        setKnownSlots(merged);
        syncFiltersFromSlots(merged);
        setClarification(result);
        setPending(null);
        const ack = describeKnownSlots(merged, result.max_total_minor);
        const question =
          result.questions[0] ?? "I still need a couple more details before I can lock a mandate.";
        addMessage("assistant", ack ? `${ack} ${question}` : question);
      } else {
        const ready = result as MandateDraftReady;
        const merged = mergeSlots(knownSlots, ready.slots);
        setKnownSlots(merged);
        syncFiltersFromSlots(merged);
        setClarification(null);
        setPending({
          maxTotalMinor: ready.max_total_minor,
          nights: ready.slots.nights ?? filters.nights,
          checkIn: ready.slots.check_in ?? defaultCheckIn(),
        });
        addMessage("assistant", "Got it — here's what I'll lock in. Review and confirm below.");
      }
    } catch {
      setLastFailedText(transcript);
      addMessage("assistant", "Sorry, I couldn't process that just now. Please try again.");
    }
  }

  async function runExtraction(nextUserText: string) {
    addMessage("user", nextUserText);
    setInputText("");
    setLastFailedText(null);
    const transcript = [...messages, { role: "user" as const, text: nextUserText, id: "", ts: 0 }]
      .filter((m) => m.role === "user")
      .map((m) => m.text)
      .join("\n");
    await attemptExtraction(transcript);
  }

  async function handleRetry() {
    if (!lastFailedText) return;
    const transcript = lastFailedText;
    setLastFailedText(null);
    await attemptExtraction(transcript);
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

  async function handleChip(chip: ChipDef) {
    if (chip.apply) setFilters((f) => ({ ...f, ...chip.apply!(f) }));
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
      rooms: knownSlots.rooms ?? filters.guests,
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

  function handleUpsellShowOptions() {
    setUpsellStage("options");
  }

  async function handleUpsellDecline() {
    setUpsellDismissed(true);
    if (activeOrder) {
      await declineUpsell.mutateAsync({ base_order_id: activeOrder.orderId });
    }
  }

  function handleUpsellSelectOffer(offer: UpsellOffer) {
    setSelectedOffer(offer);
    setUpsellStage("review");
  }

  function handleUpsellCancelReview() {
    setSelectedOffer(null);
    setUpsellStage("options");
  }

  async function handleUpsellApprove() {
    if (!activeOrder || !selectedOffer) return;
    const outcome = await purchaseUpsell.mutateAsync({
      base_order_id: activeOrder.orderId,
      offer_sku: selectedOffer.sku,
    });
    setUpsellResult({
      ok: outcome.decision === "accept",
      offer: selectedOffer,
      addonOrderId: outcome.addon_order_id,
      reasonCode: outcome.reason_code,
    });
    setUpsellStage("result");
  }

  function handleUpsellSeeOtherOptions() {
    setUpsellResult(null);
    setSelectedOffer(null);
    setUpsellStage("options");
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
            {!chatMinimized && config.data && (
              <span className="flex items-center gap-1 text-[11px] text-navy-400">
                <Sparkles size={10} />
                {config.data.llm_status === "groq_healthy"
                  ? "AI assistance available"
                  : "Using private deterministic assistance"}
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

              {lastFailedText && (
                <div className="flex max-w-[85%] items-center gap-2 rounded-2xl bg-coral-50 px-3.5 py-2.5 text-sm text-coral-700">
                  <span>Something went wrong.</span>
                  <button
                    type="button"
                    onClick={() => void handleRetry()}
                    className="flex items-center gap-1 rounded-full bg-white px-2.5 py-1 text-xs font-semibold text-coral-600 shadow-sm hover:bg-coral-50"
                  >
                    <RotateCcw size={12} /> Retry
                  </button>
                </div>
              )}

              {!mandate &&
                TRIP_DETAIL_LABELS.some(({ key }) => knownSlots[key] !== null) && (
                  <div className="rounded-2xl border border-sky-100 bg-white/70 p-3">
                    <p className="mb-1.5 text-xs font-medium text-navy-500">Trip details so far</p>
                    <div className="flex flex-wrap gap-1.5">
                      {TRIP_DETAIL_LABELS.filter(({ key }) => knownSlots[key] !== null).map(
                        ({ key, format }) => (
                          <span
                            key={key}
                            className="rounded-full bg-sky-50 px-2.5 py-1 text-xs font-medium text-navy-700"
                          >
                            {format(knownSlots[key] as never)}
                          </span>
                        ),
                      )}
                    </div>
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
                    <div className="flex items-center justify-between">
                      <dt className="text-navy-500">Destination</dt>
                      <dd className="font-medium text-navy-900">Goa</dd>
                    </div>
                    <div className="flex items-center justify-between">
                      <dt className="text-navy-500">Nights</dt>
                      <dd>
                        <input
                          type="number"
                          aria-label="Nights"
                          min={1}
                          max={14}
                          value={pending.nights}
                          onChange={(e) =>
                            setPending((p) => (p ? { ...p, nights: Number(e.target.value) } : p))
                          }
                          className="w-16 rounded-lg border border-sky-100 px-2 py-1 text-right text-sm font-medium text-navy-900"
                        />
                      </dd>
                    </div>
                    <div className="flex items-center justify-between">
                      <dt className="text-navy-500">Budget cap</dt>
                      <dd className="flex items-center gap-1">
                        <span className="text-navy-500">₹</span>
                        <input
                          type="number"
                          aria-label="Budget cap"
                          min={1000}
                          step={500}
                          value={pending.maxTotalMinor / 100}
                          onChange={(e) =>
                            setPending((p) =>
                              p ? { ...p, maxTotalMinor: Number(e.target.value) * 100 } : p,
                            )
                          }
                          className="w-24 rounded-lg border border-sky-100 px-2 py-1 text-right text-sm font-medium text-navy-900"
                        />
                      </dd>
                    </div>
                    <div className="flex items-center justify-between">
                      <dt className="text-navy-500">Max/night</dt>
                      <dd className="font-medium text-navy-900">
                        {formatMinor(Math.floor(pending.maxTotalMinor / pending.nights))}
                      </dd>
                    </div>
                    <div className="flex items-center justify-between">
                      <dt className="text-navy-500">Refundable</dt>
                      <dd className="font-medium text-navy-900">
                        {(knownSlots.refundable ?? filters.refundableOnly) ? "Required" : "Not required"}
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
                      onClick={() =>
                        navigate(`/merchant?order_id=${orderStatus.data.order_id}&panel=proof`)
                      }
                      className="rounded-full border border-sky-100 bg-white px-3 py-1.5 text-xs font-medium text-navy-700 hover:bg-sky-50"
                    >
                      View proof
                    </button>
                  </div>
                </div>
              )}

              {activeOrder &&
                orderStatus.data?.status === "CAPTURED" &&
                !upsellDismissed &&
                upsellStage === "idle" &&
                (upsellOffers.data?.offers.length ?? 0) > 0 && (
                  <div className="rounded-2xl border border-coral-100 bg-white p-4 shadow-card">
                    <p className="text-sm font-semibold text-navy-900">
                      Your Goa stay is confirmed. Want to see optional extras for this trip?
                    </p>
                    <p className="mt-1 text-xs text-navy-500">
                      Entirely optional -- nothing is added or charged unless you approve it.
                    </p>
                    <div className="mt-3 flex gap-2">
                      <button
                        type="button"
                        onClick={handleUpsellShowOptions}
                        className="flex-1 rounded-xl bg-coral-500 px-3 py-2 text-sm font-semibold text-white hover:bg-coral-600"
                      >
                        Show options
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleUpsellDecline()}
                        className="flex-1 rounded-xl border border-sky-100 px-3 py-2 text-sm font-medium text-navy-700 hover:bg-sky-50"
                      >
                        No thanks
                      </button>
                    </div>
                  </div>
                )}

              {upsellStage === "options" && (
                <div className="rounded-2xl border border-sky-100 bg-white p-4 shadow-card">
                  <p className="mb-3 text-sm font-semibold text-navy-900">Optional extras for this trip</p>
                  {upsellOffers.data && upsellOffers.data.offers.length > 0 ? (
                    <div className="space-y-2">
                      {upsellOffers.data.offers.map((offer) => (
                        <button
                          key={offer.sku}
                          type="button"
                          onClick={() => handleUpsellSelectOffer(offer)}
                          className="w-full rounded-xl border border-sky-100 p-3 text-left hover:bg-sky-50"
                        >
                          <div className="flex items-center justify-between">
                            <p className="text-sm font-medium text-navy-900">{offer.title}</p>
                            <p className="text-sm font-semibold text-navy-900">
                              {formatMinor(offer.total_minor)}
                            </p>
                          </div>
                          <p className="mt-0.5 text-xs text-navy-500">
                            {offer.quantity_description} ·{" "}
                            {offer.refundable ? "Refundable" : "Non-refundable"}
                          </p>
                        </button>
                      ))}
                    </div>
                  ) : (
                    <p className="text-sm text-navy-500">
                      Nothing left to add for this booking -- you're all set.
                    </p>
                  )}
                  <button
                    type="button"
                    onClick={() => setUpsellStage("idle")}
                    className="mt-3 text-xs font-medium text-navy-500 hover:underline"
                  >
                    Not right now
                  </button>
                </div>
              )}

              {upsellStage === "review" && selectedOffer && (
                <div className="rounded-2xl border border-coral-100 bg-white p-4 shadow-card">
                  <p className="text-sm font-semibold text-navy-900">Review: {selectedOffer.title}</p>
                  <p className="mt-1 text-xs text-navy-500">
                    This is separate from your original booking -- approving it authorizes one new,
                    bounded, single-use payment of {formatMinor(selectedOffer.total_minor)}, never a
                    reuse of your original mandate.
                  </p>
                  <dl className="mt-3 space-y-1 text-sm">
                    <div className="flex justify-between">
                      <dt className="text-navy-500">{selectedOffer.quantity_description}</dt>
                      <dd className="font-medium text-navy-900">{formatMinor(selectedOffer.total_minor)}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-navy-500">Refundable</dt>
                      <dd className="font-medium text-navy-900">
                        {selectedOffer.refundable ? "Yes" : "No"}
                      </dd>
                    </div>
                  </dl>
                  <div className="mt-3 flex gap-2">
                    <button
                      type="button"
                      onClick={() => void handleUpsellApprove()}
                      disabled={purchaseUpsell.isPending}
                      className="flex-1 rounded-xl bg-coral-500 px-3 py-2 text-sm font-semibold text-white hover:bg-coral-600 disabled:opacity-60"
                    >
                      {purchaseUpsell.isPending ? "Processing…" : "Approve"}
                    </button>
                    <button
                      type="button"
                      onClick={handleUpsellCancelReview}
                      disabled={purchaseUpsell.isPending}
                      className="flex-1 rounded-xl border border-sky-100 px-3 py-2 text-sm font-medium text-navy-700 hover:bg-sky-50 disabled:opacity-60"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}

              {upsellStage === "result" && upsellResult && (
                <div className="rounded-2xl bg-sky-50 px-3.5 py-2.5 text-sm text-navy-900">
                  {upsellResult.ok ? (
                    <>
                      <p>
                        Added — {upsellResult.offer.title} for{" "}
                        {formatMinor(upsellResult.offer.total_minor)}, settled separately under its
                        own mandate.
                      </p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={() =>
                            navigate(`/merchant?order_id=${upsellResult.addonOrderId}&panel=proof`)
                          }
                          className="rounded-full border border-sky-100 bg-white px-3 py-1.5 text-xs font-medium text-navy-700 hover:bg-sky-50"
                        >
                          View proof
                        </button>
                        {(upsellOffers.data?.offers.length ?? 0) > 0 && (
                          <button
                            type="button"
                            onClick={handleUpsellSeeOtherOptions}
                            className="rounded-full border border-sky-100 bg-white px-3 py-1.5 text-xs font-medium text-navy-700 hover:bg-sky-50"
                          >
                            See other extras
                          </button>
                        )}
                      </div>
                    </>
                  ) : (
                    <p>
                      The add-on couldn't be completed ({upsellResult.reasonCode ?? "declined"}) — your
                      original booking is unaffected.
                    </p>
                  )}
                </div>
              )}
            </div>

            {!mandate && !pending && (
              <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-t border-sky-100 px-4 py-2.5">
                {(clarification
                  ? chipsForSlot(currentQuestionSlot(clarification.missing_slots))
                  : STARTER_CHIPS
                ).map((chip) => (
                  <button
                    key={chip.label}
                    type="button"
                    onClick={() => void handleChip(chip)}
                    className="rounded-full bg-sky-50 px-3 py-1.5 text-xs font-medium text-navy-700 hover:bg-sky-100"
                  >
                    {chip.label}
                  </button>
                ))}
                {clarification && currentQuestionSlot(clarification.missing_slots) === "check_in" && (
                  <label className="flex items-center gap-1.5 rounded-full bg-sky-50 px-3 py-1.5 text-xs font-medium text-navy-700">
                    Check-in
                    <input
                      type="date"
                      aria-label="Check-in date"
                      onChange={(e) => {
                        if (e.target.value) void runExtraction(`Check in on ${e.target.value}.`);
                      }}
                      className="bg-transparent text-xs text-navy-900 focus-visible:outline-none"
                    />
                  </label>
                )}
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
