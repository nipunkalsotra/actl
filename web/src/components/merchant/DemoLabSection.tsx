import {
  CheckCircle2,
  CircleAlert,
  Cloud,
  CreditCard,
  FlaskConical,
  Play,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import { useState } from "react";
import { useDemoRun, useMerchantHealth, useStartDemoRun } from "../../api/merchantHooks";
import type { DemoRun, DemoRunScenario } from "../../api/merchantTypes";
import { explainRun, SCENARIOS, type ScenarioMeta } from "../../lib/trustLab";
import { Overlay } from "../Overlay";
import { TrustLabRunPanel } from "./TrustLabRunPanel";
import { motion } from "framer-motion";

const SCENARIO_ICON: Record<DemoRunScenario, typeof ShieldCheck> = {
  stale_price: ShieldCheck,
  declined: CreditCard,
  llm_down: Cloud,
  verify_chain: ShieldCheck,
};

const TOUR_ORDER: DemoRunScenario[] = ["stale_price", "declined", "llm_down", "verify_chain"];

function ScenarioCard({
  meta,
  disabledReason,
  onRun,
  isPending,
}: {
  meta: ScenarioMeta;
  disabledReason: string | null;
  onRun: () => void;
  isPending: boolean;
}) {
  const Icon = SCENARIO_ICON[meta.id];
  return (
    <div className="flex flex-col rounded-2xl border border-sky-100 bg-card p-5 shadow-card">
      <div className="flex items-center gap-2">
        <span className="flex h-9 w-9 items-center justify-center rounded-full bg-sky-100 text-navy-700">
          <Icon size={16} />
        </span>
        <p className="text-sm font-semibold text-navy-900">{meta.title}</p>
      </div>

      <dl className="mt-3 flex-1 space-y-2.5 text-xs">
        <div>
          <dt className="font-medium text-navy-500">What goes wrong</dt>
          <dd className="mt-0.5 text-navy-700">{meta.whatGoesWrong}</dd>
        </div>
        <div>
          <dt className="font-medium text-navy-500">What ACTL detects</dt>
          <dd className="mt-0.5 text-navy-700">{meta.whatDetects}</dd>
        </div>
        <div>
          <dt className="font-medium text-navy-500">How ACTL contains it</dt>
          <dd className="mt-0.5 text-navy-700">{meta.howContains}</dd>
        </div>
      </dl>

      <button
        type="button"
        disabled={disabledReason !== null || isPending}
        onClick={onRun}
        className="mt-4 flex w-full items-center justify-center gap-1.5 rounded-xl bg-ink px-3 py-2 text-sm font-semibold text-on-ink hover:bg-ink-hover disabled:cursor-not-allowed disabled:bg-sky-100 disabled:text-navy-500"
      >
        <Play size={14} />
        {isPending ? "Starting…" : "Run this scenario"}
      </button>
      {disabledReason && <p className="mt-2 text-xs text-coral-400">{disabledReason}</p>}
    </div>
  );
}

function TourSummary({ runs, onClose }: { runs: DemoRun[]; onClose: () => void }) {
  return (
    <Overlay open onClose={onClose}>
      <motion.div
        role="dialog"
        aria-modal="true"
        aria-label="Trust tour summary"
        initial={{ opacity: 0, scale: 0.96 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.96 }}
        className="fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-3xl bg-card shadow-float"
      >
        <div className="flex items-center justify-between border-b border-sky-100 px-5 py-4">
          <h2 className="flex items-center gap-2 text-base font-semibold text-navy-900">
            <Sparkles size={16} className="text-ocean-400" /> Trust tour complete
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close tour summary"
            className="flex h-8 w-8 items-center justify-center rounded-full text-navy-500 hover:bg-sky-50"
          >
            <X size={16} />
          </button>
        </div>
        <div className="max-h-[70vh] space-y-3 overflow-y-auto p-5">
          {runs.map((run) => {
            const explanation = explainRun(run);
            const ok = run.status === "passed";
            return (
              <div key={run.run_id} className="rounded-xl border border-sky-100 p-3">
                <div className="flex items-center gap-2">
                  {ok ? (
                    <CheckCircle2 size={15} className="text-emerald-600" />
                  ) : (
                    <CircleAlert size={15} className="text-coral-700" />
                  )}
                  <p className="text-sm font-semibold text-navy-900">
                    {run.scenario.replace(/_/g, " ")}
                  </p>
                </div>
                {explanation && (
                  <p className="mt-1 text-xs text-navy-500">{explanation.finalState}</p>
                )}
              </div>
            );
          })}
        </div>
      </motion.div>
    </Overlay>
  );
}

interface DemoLabSectionProps {
  activeRunId: string | null;
  onRunIdChange: (runId: string | null) => void;
  onOpenOrder: (orderId: string) => void;
  onOpenAuditSection: () => void;
}

export function DemoLabSection({
  activeRunId,
  onRunIdChange,
  onOpenOrder,
  onOpenAuditSection,
}: DemoLabSectionProps) {
  const health = useMerchantHealth();
  const [dismissedGuardNotice, setDismissedGuardNotice] = useState(false);
  const startRun = useStartDemoRun();
  const { data: activeRun } = useDemoRun(activeRunId);

  const [tourIndex, setTourIndex] = useState<number | null>(null);
  const [tourRuns, setTourRuns] = useState<DemoRun[]>([]);
  const [tourSummary, setTourSummary] = useState<DemoRun[] | null>(null);

  const notSimulator = health.data ? health.data.payment_mode !== "simulator" : false;
  const disabledReason = notSimulator
    ? "Trust Lab requires PAYMENT_PROVIDER=simulator in this environment."
    : null;

  function runScenario(scenario: DemoRunScenario) {
    startRun.mutate(scenario, {
      onSuccess: (run) => onRunIdChange(run.run_id),
    });
  }

  function startTour() {
    setTourRuns([]);
    setTourIndex(0);
    runScenario(TOUR_ORDER[0]);
  }

  function advanceTour() {
    if (tourIndex === null || !activeRun) return;
    const results = [...tourRuns, activeRun];
    const next = tourIndex + 1;
    if (next >= TOUR_ORDER.length) {
      setTourRuns([]);
      setTourIndex(null);
      setTourSummary(results);
      onRunIdChange(null);
      return;
    }
    setTourRuns(results);
    setTourIndex(next);
    runScenario(TOUR_ORDER[next]);
  }

  const isTouring = tourIndex !== null;
  const isLastTourStep = tourIndex === TOUR_ORDER.length - 1;
  const activeIsTerminal = activeRun?.status === "passed" || activeRun?.status === "failed";

  return (
    <div className="space-y-4">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-semibold text-navy-900">
          <FlaskConical className="text-coral-400" size={22} />
          ACTL Trust Lab
        </h1>
        <p className="mt-1 text-sm text-navy-500">
          Watch the trust controls work against real simulated failures.
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <span className="flex items-center gap-1.5 rounded-full bg-emerald-100 px-3 py-1 text-xs font-medium text-emerald-600">
            <ShieldCheck size={13} /> Safe local simulator
          </span>
          <span className="text-xs text-navy-500">
            Runs use local test data only. No real customer or payment data.
          </span>
        </div>
      </div>

      {notSimulator && !dismissedGuardNotice && (
        <div className="flex items-start justify-between gap-3 rounded-2xl border border-coral-100 bg-coral-100/40 p-4 text-sm text-navy-900">
          <p>
            This environment is not configured local/simulator-safe, so Trust Lab runs are
            disabled. The backend rejects every run regardless of what this page shows.
          </p>
          <button
            type="button"
            onClick={() => setDismissedGuardNotice(true)}
            className="shrink-0 text-xs font-medium text-navy-700 hover:underline"
          >
            Dismiss
          </button>
        </div>
      )}

      {activeRunId && !isTouring && activeIsTerminal && (
        <div className="flex items-center justify-between gap-3 rounded-2xl border border-sky-100 bg-sky-50 px-4 py-3 text-sm">
          <span className="text-navy-700">
            Last run: <span className="font-medium text-navy-900">{activeRun?.scenario.replace(/_/g, " ")}</span>
            {" -- "}
            {activeRun?.result?.terminal_outcome}
          </span>
          <button
            type="button"
            onClick={() => onRunIdChange(activeRunId)}
            className="shrink-0 text-xs font-medium text-ocean-400 hover:underline"
          >
            View run
          </button>
        </div>
      )}

      <button
        type="button"
        disabled={disabledReason !== null || startRun.isPending}
        onClick={startTour}
        className="flex w-full items-center justify-center gap-2 rounded-2xl border-2 border-dashed border-ocean-100 bg-ocean-100/40 px-4 py-3 text-sm font-semibold text-ocean-600 hover:bg-ocean-100 disabled:cursor-not-allowed disabled:opacity-60 sm:w-auto"
      >
        <Sparkles size={15} />
        Play full trust tour
      </button>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {SCENARIOS.map((meta) => (
          <ScenarioCard
            key={meta.id}
            meta={meta}
            disabledReason={disabledReason}
            isPending={startRun.isPending && startRun.variables === meta.id}
            onRun={() => runScenario(meta.id)}
          />
        ))}
      </div>

      <TrustLabRunPanel
        runId={activeRunId}
        onClose={() => onRunIdChange(null)}
        onRunAgain={() => activeRun && runScenario(activeRun.scenario as DemoRunScenario)}
        onOpenOrder={onOpenOrder}
        onInspectAudit={() => {
          onRunIdChange(null);
          onOpenAuditSection();
        }}
        nextLabel={
          isTouring && activeIsTerminal
            ? isLastTourStep
              ? "View tour summary"
              : `Next: ${TOUR_ORDER[tourIndex + 1].replace(/_/g, " ")}`
            : undefined
        }
        onNext={isTouring && activeIsTerminal ? advanceTour : undefined}
      />

      {tourSummary && <TourSummary runs={tourSummary} onClose={() => setTourSummary(null)} />}
    </div>
  );
}
