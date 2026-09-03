import { motion, useReducedMotion } from "framer-motion";
import {
  CheckCircle2,
  CircleAlert,
  Clock,
  Loader2,
  ReceiptText,
  RotateCcw,
  ShieldCheck,
  Undo2,
  X,
} from "lucide-react";
import { useDemoRun } from "../../api/merchantHooks";
import type { DemoEvent, DemoEventStatus, DemoRun } from "../../api/merchantTypes";
import {
  aggregateCheckpointEvents,
  deriveTrustControls,
  explainRun,
  groupStalePriceAttempts,
  orderIdFromRun,
  SCENARIO_BY_ID,
  type CheckpointAggregate,
} from "../../lib/trustLab";
import { Overlay } from "../Overlay";

const STATUS_ICON: Record<DemoEventStatus, typeof CheckCircle2> = {
  pending: Clock,
  running: Loader2,
  passed: CheckCircle2,
  blocked: CircleAlert,
  failed: CircleAlert,
  compensated: Undo2,
};

const STATUS_TONE: Record<DemoEventStatus, string> = {
  pending: "bg-sky-100 text-navy-500",
  running: "bg-ocean-100 text-ocean-600",
  passed: "bg-emerald-100 text-emerald-600",
  blocked: "bg-coral-100 text-coral-700",
  failed: "bg-coral-100 text-coral-700",
  compensated: "bg-coral-100 text-coral-700",
};

function EvidenceRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 text-xs">
      <span className="shrink-0 text-navy-500">{label}</span>
      <span className="truncate font-mono text-navy-700" title={value}>
        {value}
      </span>
    </div>
  );
}

function EventRow({ event, index }: { event: DemoEvent; index: number }) {
  const prefersReducedMotion = useReducedMotion();
  const Icon = STATUS_ICON[event.status];
  const isRunning = event.status === "running";
  const ev = event.evidence;

  return (
    <motion.li
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: prefersReducedMotion ? 0 : Math.min(index * 0.04, 0.4) }}
      className="flex items-start gap-3"
    >
      <span
        className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${STATUS_TONE[event.status]}`}
      >
        <motion.span
          animate={isRunning && !prefersReducedMotion ? { rotate: 360 } : undefined}
          transition={isRunning ? { repeat: Infinity, duration: 1.1, ease: "linear" } : undefined}
        >
          <Icon size={16} />
        </motion.span>
      </span>
      <div className="min-w-0 flex-1 pb-1">
        <p className="text-sm font-medium text-navy-900">{event.title}</p>
        <p className="mt-0.5 text-xs text-navy-500">{event.detail}</p>
        {(ev.audit_seq !== undefined || ev.entry_hash_prefix !== undefined) && (
          <div className="mt-1.5 space-y-0.5 rounded-lg bg-sky-50 p-2">
            {ev.audit_seq !== undefined && <EvidenceRow label="audit seq" value={String(ev.audit_seq)} />}
            {ev.entry_hash_prefix !== undefined && (
              <EvidenceRow label="hash" value={ev.entry_hash_prefix} />
            )}
            {ev.gate !== undefined && <EvidenceRow label="gate" value={ev.gate} />}
            {ev.reason_code !== undefined && <EvidenceRow label="reason" value={ev.reason_code} />}
          </div>
        )}
      </div>
    </motion.li>
  );
}

function AttemptGroupHeader({ label }: { label: string }) {
  return (
    <li className="flex items-center gap-2 pt-2 first:pt-0">
      <span className="h-px flex-1 bg-sky-100" aria-hidden="true" />
      <span className="shrink-0 text-[11px] font-semibold uppercase tracking-wide text-navy-500">
        {label}
      </span>
      <span className="h-px flex-1 bg-sky-100" aria-hidden="true" />
    </li>
  );
}

function CheckpointSummaryRow({
  aggregate,
  index,
}: {
  aggregate: CheckpointAggregate;
  index: number;
}) {
  const prefersReducedMotion = useReducedMotion();
  const hasConflict = aggregate.conflict > 0;
  const Icon = hasConflict ? CircleAlert : CheckCircle2;

  return (
    <motion.li
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: prefersReducedMotion ? 0 : Math.min(index * 0.04, 0.4) }}
      className="flex items-start gap-3"
    >
      <span
        className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${
          hasConflict ? "bg-coral-100 text-coral-700" : "bg-emerald-100 text-emerald-600"
        }`}
      >
        <Icon size={16} />
      </span>
      <div className="min-w-0 flex-1 pb-1">
        <p className="text-sm font-medium text-navy-900">{aggregate.total} checkpoints matched</p>
        <p className="mt-0.5 text-xs text-navy-500">
          {aggregate.anchored} anchored, {aggregate.unanchored} not anchored
          {hasConflict ? `, ${aggregate.conflict} CONFLICT` : ""}.
        </p>
        <details className="mt-1.5 rounded-xl border border-sky-100 bg-sky-50 p-2 text-xs">
          <summary className="cursor-pointer font-medium text-navy-700">
            Show all {aggregate.total} checkpoints
          </summary>
          <div className="mt-2 space-y-2">
            {aggregate.checkpoints.map((cp) => (
              <div key={cp.seq} className="border-t border-sky-100 pt-2 first:border-t-0 first:pt-0">
                <p className="font-medium text-navy-900">{cp.title}</p>
                <p className="mt-0.5 text-navy-500">{cp.detail}</p>
              </div>
            ))}
          </div>
        </details>
      </div>
    </motion.li>
  );
}

/** Real event data is never reshaped here -- only how it's grouped for
 * display. Stale-price splits into its two real attempts; verify-chain
 * rolls its (potentially dozens of) real per-checkpoint events into one
 * truthful summary row with every real checkpoint still inspectable inside
 * it. Every other scenario renders exactly as before: a flat event list. */
function renderTimeline(run: DemoRun) {
  const groups = groupStalePriceAttempts(run);
  if (groups) {
    let i = 0;
    return groups.flatMap((group) => [
      <AttemptGroupHeader key={`header-${group.label}`} label={group.label} />,
      ...group.events.map((event) => <EventRow key={event.seq} event={event} index={i++} />),
    ]);
  }

  const checkpointAgg = aggregateCheckpointEvents(run.events);
  if (checkpointAgg) {
    const checkpointSeqs = new Set(checkpointAgg.checkpoints.map((e) => e.seq));
    let summaryInserted = false;
    let i = 0;
    return run.events.flatMap((event) => {
      if (checkpointSeqs.has(event.seq)) {
        if (summaryInserted) return [];
        summaryInserted = true;
        return [<CheckpointSummaryRow key="checkpoint-summary" aggregate={checkpointAgg} index={i++} />];
      }
      return [<EventRow key={event.seq} event={event} index={i++} />];
    });
  }

  return run.events.map((event, i) => <EventRow key={event.seq} event={event} index={i} />);
}

function TrustControlsPanel({ runId }: { runId: string }) {
  const { data: run } = useDemoRun(runId);
  if (!run) return null;
  const controls = deriveTrustControls(run);

  const rows: [string, string][] = [
    ["Mandate", controls.mandate],
    ["Quote", controls.quote],
    ["Gate", controls.gate],
    ["Payment", controls.payment],
    ["Ledger", controls.ledger],
    ["Audit", controls.audit],
  ];

  return (
    <dl className="space-y-3">
      {rows.map(([label, value]) => (
        <div key={label} className="rounded-xl border border-sky-100 bg-sky-50 px-3 py-2">
          <dt className="text-[11px] font-medium uppercase tracking-wide text-navy-500">{label}</dt>
          <dd className="mt-0.5 text-sm font-medium text-navy-900">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

interface TrustLabRunPanelProps {
  runId: string | null;
  onClose: () => void;
  onRunAgain: () => void;
  onOpenOrder: (orderId: string) => void;
  onInspectAudit: () => void;
  nextLabel?: string;
  onNext?: () => void;
}

export function TrustLabRunPanel({
  runId,
  onClose,
  onRunAgain,
  onOpenOrder,
  onInspectAudit,
  nextLabel,
  onNext,
}: TrustLabRunPanelProps) {
  const { data: run } = useDemoRun(runId);
  const meta = run ? SCENARIO_BY_ID[run.scenario as keyof typeof SCENARIO_BY_ID] : undefined;
  const explanation = run ? explainRun(run) : null;
  const orderId = run ? orderIdFromRun(run) : null;
  const isTerminal = run?.status === "passed" || run?.status === "failed";

  return (
    <Overlay open={runId !== null} onClose={onClose}>
      <motion.div
        role="dialog"
        aria-modal="true"
        aria-label="Trust Lab run"
        initial={{ x: "100%" }}
        animate={{ x: 0 }}
        exit={{ x: "100%" }}
        transition={{ type: "spring", stiffness: 300, damping: 30 }}
        className="fixed inset-y-0 right-0 z-50 flex w-full max-w-full flex-col overflow-hidden bg-card shadow-float sm:max-w-3xl sm:rounded-l-3xl lg:max-w-5xl"
      >
        <div className="flex items-center justify-between border-b border-sky-100 px-5 py-4">
          <div className="min-w-0">
            <p className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-navy-500">
              <ShieldCheck size={12} className="text-emerald-600" /> Safe local simulator
            </p>
            <h2 className="truncate text-base font-semibold text-navy-900">
              {meta?.title ?? "Trust Lab run"}
            </h2>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <span
              className={`rounded-full px-2.5 py-1 text-xs font-medium ${
                run?.status === "passed"
                  ? "bg-emerald-100 text-emerald-600"
                  : run?.status === "failed"
                    ? "bg-coral-100 text-coral-700"
                    : "bg-sky-100 text-navy-500"
              }`}
            >
              {run?.status ?? "queued"}
            </span>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close Trust Lab run"
              className="flex h-8 w-8 items-center justify-center rounded-full text-navy-500 hover:bg-sky-50"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        <div className="grid flex-1 grid-cols-1 overflow-hidden lg:grid-cols-[1fr_320px]">
          <div className="overflow-y-auto p-5">
            {!run && <p className="text-sm text-navy-500">Loading…</p>}
            {run && (
              <ol className="space-y-4">
                {renderTimeline(run)}
                {run.status === "queued" && (
                  <li className="flex items-center gap-2 text-sm text-navy-500">
                    <Loader2 size={15} className="animate-spin" /> Starting…
                  </li>
                )}
              </ol>
            )}

            {run?.error && (
              <p className="mt-4 flex items-center gap-1.5 rounded-xl bg-coral-100 px-3 py-2 text-xs text-coral-700">
                <CircleAlert size={13} /> {run.error}
              </p>
            )}

            {explanation && (
              <div className="mt-6 space-y-3 rounded-2xl border border-sky-100 bg-sky-50 p-4">
                <div>
                  <p className="text-[11px] font-medium uppercase tracking-wide text-navy-500">
                    Detected
                  </p>
                  <p className="text-sm text-navy-900">{explanation.detected}</p>
                </div>
                <div>
                  <p className="text-[11px] font-medium uppercase tracking-wide text-navy-500">
                    Contained
                  </p>
                  <p className="text-sm text-navy-900">{explanation.contained}</p>
                </div>
                <div>
                  <p className="text-[11px] font-medium uppercase tracking-wide text-navy-500">
                    Final state
                  </p>
                  <p className="text-sm font-semibold text-navy-900">{explanation.finalState}</p>
                </div>
                <div>
                  <p className="text-[11px] font-medium uppercase tracking-wide text-navy-500">
                    Why the buyer is protected
                  </p>
                  <p className="text-sm text-navy-900">{explanation.whyProtected}</p>
                </div>
              </div>
            )}
          </div>

          <div className="border-t border-sky-100 bg-card p-5 lg:border-l lg:border-t-0">
            <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-navy-500">
              Trust controls
            </p>
            {runId && <TrustControlsPanel runId={runId} />}
          </div>
        </div>

        {isTerminal && (
          <div className="flex flex-wrap items-center gap-2 border-t border-sky-100 px-5 py-4">
            {orderId && (
              <button
                type="button"
                onClick={() => onOpenOrder(orderId)}
                className="flex items-center gap-1.5 rounded-full bg-ocean-600 px-4 py-2 text-sm font-semibold text-white hover:bg-ocean-500"
              >
                <ReceiptText size={14} /> Open order proof
              </button>
            )}
            <button
              type="button"
              onClick={onInspectAudit}
              className="flex items-center gap-1.5 rounded-full border border-sky-100 px-4 py-2 text-sm font-medium text-navy-700 hover:bg-sky-50"
            >
              <ShieldCheck size={14} /> Inspect audit timeline
            </button>
            <button
              type="button"
              onClick={onRunAgain}
              className="flex items-center gap-1.5 rounded-full border border-sky-100 px-4 py-2 text-sm font-medium text-navy-700 hover:bg-sky-50"
            >
              <RotateCcw size={14} /> Run again
            </button>
            {nextLabel && onNext ? (
              <button
                type="button"
                onClick={onNext}
                className="ml-auto rounded-full bg-coral-500 px-4 py-2 text-sm font-semibold text-white hover:bg-coral-600"
              >
                {nextLabel}
              </button>
            ) : (
              <button
                type="button"
                onClick={onClose}
                className="ml-auto text-sm font-medium text-ocean-400 hover:underline"
              >
                Back to scenarios
              </button>
            )}
          </div>
        )}
      </motion.div>
    </Overlay>
  );
}
