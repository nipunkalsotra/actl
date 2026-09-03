import type { DemoEvent, DemoRun, DemoRunScenario } from "../api/merchantTypes";
import { formatMinor } from "./money";

export interface ScenarioMeta {
  id: DemoRunScenario;
  title: string;
  whatGoesWrong: string;
  whatDetects: string;
  howContains: string;
}

export const SCENARIOS: ScenarioMeta[] = [
  {
    id: "stale_price",
    title: "Stale price",
    whatGoesWrong: "A price changes out-of-band after a quote is already locked in.",
    whatDetects: "Gate G5 catches the mismatch between the quote's pinned catalog version and the live one.",
    howContains:
      "The stale purchase is blocked before payment. A fresh quote is issued automatically and the buyer completes their purchase at the real, current price.",
  },
  {
    id: "declined",
    title: "Payment declined",
    whatGoesWrong: "The payment provider declines the charge.",
    whatDetects: "The simulator reports a real decline -- ACTL never treats a failed charge as success.",
    howContains:
      "The held reservation is released back to the mandate's available balance. The buyer is never charged for a payment that failed.",
  },
  {
    id: "llm_down",
    title: "LLM unavailable",
    whatGoesWrong: "The LLM used for mandate extraction and ranking goes down.",
    whatDetects: "Every LLM call fails by design in this run.",
    howContains:
      "A deterministic, non-LLM fallback path takes over immediately. No money decision is ever delegated to the LLM -- the purchase still completes safely.",
  },
  {
    id: "verify_chain",
    title: "Verify audit chain",
    whatGoesWrong: "N/A -- this is a live integrity check, not an injected fault.",
    whatDetects: "Every entry's hash chain is independently recomputed from scratch, right now.",
    howContains:
      "Any tampering would be caught immediately. Anchoring status is reported honestly -- never claimed unless a checkpoint is genuinely anchored.",
  },
];

export const SCENARIO_BY_ID: Record<DemoRunScenario, ScenarioMeta> = Object.fromEntries(
  SCENARIOS.map((s) => [s.id, s]),
) as Record<DemoRunScenario, ScenarioMeta>;

// ---------------------------------------------------------------------------
// Trust controls panel -- every value below is derived from real events
// already returned by the backend (audit seq, reason codes, real amounts),
// never invented client-side.
// ---------------------------------------------------------------------------

export interface TrustControls {
  mandate: string;
  quote: string;
  gate: string;
  payment: string;
  ledger: string;
  audit: string;
}

function centsLabel(minor: number | undefined): string {
  return minor === undefined ? "—" : formatMinor(minor);
}

export function deriveTrustControls(run: DemoRun): TrustControls {
  let mandate = "Not started";
  let quote = "Not started";
  let gate = "Not reached";
  let payment = "Not started";
  let reservedAtStart: number | undefined;
  let reservedAtEnd: number | undefined;
  let audit = run.events.length > 0 ? "Recording" : "Not started";

  for (const e of run.events) {
    switch (e.kind) {
      case "mandate.locked":
        mandate = "Locked";
        break;
      case "quote.issued":
        quote = "Current";
        break;
      case "catalog.price_mutated":
        quote = "Stale (catalog moved after this quote)";
        break;
      case "order.proposed.denied":
        gate = `${e.evidence.gate ?? "?"} denied -- ${e.evidence.reason_code ?? "?"}`;
        if (e.evidence.reason_code === "STALE_PRICE") quote = "Stale";
        break;
      case "order.proposed.allowed":
        gate = "G1-G7 allowed -- OK";
        quote = "Current";
        break;
      case "budget.reserved":
        payment = "Not started";
        reservedAtStart = e.evidence.reserved_balance_minor;
        reservedAtEnd = e.evidence.reserved_balance_minor;
        break;
      case "payment.intent":
        payment = "Authorizing";
        break;
      case "payment.result":
        payment = e.evidence.payment_state === "captured" ? "Captured" : "Declined";
        break;
      case "settlement.closed":
        payment = "Captured";
        reservedAtEnd = e.evidence.reserved_balance_minor ?? 0;
        break;
      case "compensation.applied":
        payment = "Declined";
        reservedAtEnd = e.evidence.reserved_balance_minor ?? 0;
        break;
      case "chain.entries_verified":
        audit = e.status === "passed" ? "Chain verified" : "Chain BROKEN";
        break;
      case "checkpoint.merkle_matched":
        audit = `Chain verified -- anchor: ${e.evidence.checkpoint_status ?? "unknown"}`;
        break;
      default:
        break;
    }
  }

  if (run.result) {
    if (run.result.chain_verified === true) audit = audit === "Recording" ? "Chain verified" : audit;
    if (run.result.chain_verified === false) audit = "Chain BROKEN";
  }

  const ledger =
    reservedAtStart === undefined
      ? "No reservation taken"
      : `${centsLabel(reservedAtStart)} → ${centsLabel(reservedAtEnd)}`;

  return { mandate, quote, gate, payment, ledger, audit };
}

// ---------------------------------------------------------------------------
// Detected / Contained / Final state / Why the buyer is protected -- built
// only from this run's own real result + events, per scenario.
// ---------------------------------------------------------------------------

export interface RunExplanation {
  detected: string;
  contained: string;
  finalState: string;
  whyProtected: string;
}

export function explainRun(run: DemoRun): RunExplanation | null {
  const result = run.result;
  if (!result) return null;

  const compensation = run.events.find((e) => e.kind === "compensation.applied");
  const denied = run.events.find((e) => e.kind === "order.proposed.denied");
  const settlement = run.events.find((e) => e.kind === "settlement.closed");

  switch (run.scenario) {
    case "stale_price": {
      const firstQuote = run.events.find((e) => e.kind === "quote.issued");
      const mutation = run.events.find((e) => e.kind === "catalog.price_mutated");
      const originalVersion = firstQuote?.evidence.catalog_version;
      const mutatedVersion = mutation?.evidence.catalog_version;
      return {
        detected:
          originalVersion !== undefined && mutatedVersion !== undefined
            ? `Catalog moved from version ${originalVersion} to ${mutatedVersion} after the quote was pinned -- Gate ${
                denied?.evidence.gate ?? "G5"
              } caught the mismatch as ${denied?.evidence.reason_code ?? "STALE_PRICE"}.`
            : (result.detected_fault ?? "—"),
        contained: result.recovery_action,
        finalState:
          denied !== undefined
            ? "Stale quote blocked before payment. Fresh quote issued. Safe retry completed."
            : result.terminal_outcome,
        whyProtected:
          denied !== undefined
            ? "The rejected, stale-priced attempt never reached payment -- no capture occurred for it. The purchase only completed after being re-quoted at the real, current price."
            : "This run never actually hit a stale quote.",
      };
    }
    case "declined":
      return {
        detected: result.detected_fault ?? "—",
        contained: result.recovery_action,
        finalState: result.terminal_outcome,
        whyProtected:
          compensation !== undefined
            ? `${centsLabel(compensation.evidence.released_balance_minor)} held for this purchase was released back to the mandate -- the buyer was never charged for a payment that failed.`
            : "This run never actually hit a decline.",
      };
    case "llm_down":
      return {
        detected: result.detected_fault ?? "—",
        contained: result.recovery_action,
        finalState: result.terminal_outcome,
        whyProtected:
          settlement !== undefined
            ? "No money decision was ever delegated to the LLM -- the deterministic fallback path made every extraction/ranking call, and the purchase completed exactly as it would have with the LLM up."
            : "The deterministic fallback ran, but this purchase didn't reach settlement.",
      };
    case "verify_chain":
      return {
        detected: "Live integrity check -- not a fault scenario.",
        contained: result.recovery_action,
        finalState: result.terminal_outcome,
        whyProtected:
          result.terminal_outcome === "CHAIN VALID"
            ? "Every entry's hash independently recomputes to the same value already on record -- nothing in this chain has been tampered with."
            : "A break was found -- see the flagged entry in the timeline below.",
      };
    default:
      return null;
  }
}

export function orderIdFromRun(run: DemoRun): string | null {
  return run.order_id ?? run.result?.order_id ?? null;
}

export function lastEventBySeq(events: DemoEvent[]): DemoEvent | null {
  return events.length === 0 ? null : events[events.length - 1];
}

// ---------------------------------------------------------------------------
// Stale-price storytelling: the timeline holds one real event stream, but it
// contains two distinct purchase attempts (the rejected stale one, then the
// re-quoted retry) -- split on the real order.proposed.denied event so the
// UI can render them as clearly separate groups instead of one flat list.
// ---------------------------------------------------------------------------

export interface StalePriceAttemptGroup {
  label: string;
  events: DemoEvent[];
}

export function groupStalePriceAttempts(run: DemoRun): StalePriceAttemptGroup[] | null {
  if (run.scenario !== "stale_price") return null;
  const deniedIdx = run.events.findIndex((e) => e.kind === "order.proposed.denied");
  if (deniedIdx === -1) return null;
  return [
    { label: "Attempt 1 -- stale quote (rejected)", events: run.events.slice(0, deniedIdx + 1) },
    { label: "Attempt 2 -- safe retry (completed)", events: run.events.slice(deniedIdx + 1) },
  ];
}

// ---------------------------------------------------------------------------
// Verify-chain storytelling: a real chain can carry dozens of checkpoints,
// each currently arriving as its own timeline event. Roll them into one
// truthful summary (real counts, nothing invented) for the main timeline;
// the individual events themselves are untouched and still available for an
// expandable "detailed checkpoints" section.
// ---------------------------------------------------------------------------

export interface CheckpointAggregate {
  total: number;
  anchored: number;
  unanchored: number;
  conflict: number;
  checkpoints: DemoEvent[];
}

export function aggregateCheckpointEvents(events: DemoEvent[]): CheckpointAggregate | null {
  const checkpoints = events.filter((e) => e.kind === "checkpoint.merkle_matched");
  if (checkpoints.length === 0) return null;
  const anchored = checkpoints.filter((e) => e.evidence.checkpoint_status === "anchored").length;
  const conflict = checkpoints.filter((e) => e.evidence.checkpoint_status === "conflict").length;
  return {
    total: checkpoints.length,
    anchored,
    unanchored: checkpoints.length - anchored - conflict,
    conflict,
    checkpoints,
  };
}
