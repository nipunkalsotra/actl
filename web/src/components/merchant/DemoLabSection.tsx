import {
  CheckCircle2,
  Cloud,
  CreditCard,
  FlaskConical,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import { useState } from "react";
import { ApiError } from "../../api/client";
import {
  useMerchantHealth,
  useRunDemoScenario,
  useRunVerifyChainDemo,
  type DemoScenarioEndpoint,
} from "../../api/merchantHooks";
import type { DemoResultResponse, DemoVerifyChainResponse } from "../../api/merchantTypes";
import { formatMinor } from "../../lib/money";

function scenarioEvidence(result: DemoResultResponse) {
  return (
    <dl className="mt-3 space-y-1 text-xs text-navy-700">
      <div className="flex justify-between">
        <dt className="text-navy-500">Detected fault</dt>
        <dd className="font-medium">{result.detected_fault ?? "—"}</dd>
      </div>
      <div className="flex justify-between">
        <dt className="text-navy-500">Terminal outcome</dt>
        <dd className="font-medium">{result.terminal_outcome}</dd>
      </div>
      <div className="flex justify-between">
        <dt className="text-navy-500">Recovery</dt>
        <dd className="text-right font-medium">{result.recovery_action}</dd>
      </div>
      <div className="flex justify-between">
        <dt className="text-navy-500">Reserved balance</dt>
        <dd className="font-medium">{formatMinor(result.reserved_balance_minor)}</dd>
      </div>
      <div className="flex justify-between">
        <dt className="text-navy-500">Audit chain</dt>
        <dd className="font-medium">
          {result.chain_verified === true
            ? `Verified (${result.entries_verified} entries)`
            : result.chain_verified === false
              ? "NOT verified"
              : "—"}
        </dd>
      </div>
    </dl>
  );
}

function verifyChainEvidence(result: DemoVerifyChainResponse) {
  return (
    <dl className="mt-3 space-y-1 text-xs text-navy-700">
      <div className="flex justify-between">
        <dt className="text-navy-500">Result</dt>
        <dd className={`font-medium ${result.ok ? "text-emerald-600" : "text-coral-600"}`}>
          {result.ok ? "VALID" : "BROKEN"}
        </dd>
      </div>
      <div className="flex justify-between">
        <dt className="text-navy-500">Range</dt>
        <dd className="font-medium">
          {result.from_seq ?? "—"}–{result.to_seq ?? "—"}
        </dd>
      </div>
      <div className="flex justify-between">
        <dt className="text-navy-500">Entries verified</dt>
        <dd className="font-medium">{result.entries_verified}</dd>
      </div>
    </dl>
  );
}

function DemoCardShell({
  title,
  description,
  icon: Icon,
  children,
}: {
  title: string;
  description: string;
  icon: typeof CreditCard;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-sky-100 bg-white p-5 shadow-card">
      <div className="flex items-center gap-2">
        <span className="flex h-9 w-9 items-center justify-center rounded-full bg-sky-100 text-navy-700">
          <Icon size={16} />
        </span>
        <p className="text-sm font-semibold text-navy-900">{title}</p>
      </div>
      <p className="mt-2 text-xs text-navy-500">{description}</p>
      <span className="mt-2 inline-block rounded-full bg-coral-100 px-2 py-0.5 text-xs font-medium text-coral-600">
        Demo only
      </span>
      {children}
    </div>
  );
}

function DemoRunError({ error }: { error: unknown }) {
  return (
    <p className="mt-2 flex items-center gap-1.5 text-xs text-coral-600">
      <TriangleAlert size={13} />
      {error instanceof ApiError && typeof error.detail === "string"
        ? error.detail
        : "This demo run failed."}
    </p>
  );
}

interface ScenarioCardProps {
  title: string;
  description: string;
  icon: typeof CreditCard;
  endpoint: DemoScenarioEndpoint;
  disabledReason: string | null;
}

function ScenarioCard({ title, description, icon, endpoint, disabledReason }: ScenarioCardProps) {
  const mutation = useRunDemoScenario();

  return (
    <DemoCardShell title={title} description={description} icon={icon}>
      <button
        type="button"
        disabled={disabledReason !== null || mutation.isPending}
        onClick={() => mutation.mutate(endpoint)}
        className="mt-4 w-full rounded-xl bg-navy-900 px-3 py-2 text-sm font-semibold text-white hover:bg-navy-700 disabled:cursor-not-allowed disabled:bg-sky-100 disabled:text-navy-500"
      >
        {mutation.isPending ? "Running…" : "Run demo"}
      </button>

      {disabledReason && <p className="mt-2 text-xs text-coral-600">{disabledReason}</p>}

      {mutation.isSuccess && (
        <div className="mt-2 rounded-xl bg-emerald-100/60 p-3">
          <p className="flex items-center gap-1.5 text-xs font-semibold text-emerald-600">
            <CheckCircle2 size={13} /> Completed
          </p>
          {scenarioEvidence(mutation.data)}
        </div>
      )}

      {mutation.isError && <DemoRunError error={mutation.error} />}
    </DemoCardShell>
  );
}

function VerifyChainCard({ disabledReason }: { disabledReason: string | null }) {
  const mutation = useRunVerifyChainDemo();

  return (
    <DemoCardShell
      title="Verify audit chain"
      description="Recomputes every entry and checkpoint hash in the current chain, live."
      icon={ShieldCheck}
    >
      <button
        type="button"
        disabled={disabledReason !== null || mutation.isPending}
        onClick={() => mutation.mutate()}
        className="mt-4 w-full rounded-xl bg-navy-900 px-3 py-2 text-sm font-semibold text-white hover:bg-navy-700 disabled:cursor-not-allowed disabled:bg-sky-100 disabled:text-navy-500"
      >
        {mutation.isPending ? "Verifying…" : "Run demo"}
      </button>

      {disabledReason && <p className="mt-2 text-xs text-coral-600">{disabledReason}</p>}

      {mutation.isSuccess && (
        <div
          className={`mt-2 rounded-xl p-3 ${mutation.data.ok ? "bg-emerald-100/60" : "bg-coral-100/60"}`}
        >
          <p
            className={`flex items-center gap-1.5 text-xs font-semibold ${
              mutation.data.ok ? "text-emerald-600" : "text-coral-600"
            }`}
          >
            {mutation.data.ok ? <CheckCircle2 size={13} /> : <TriangleAlert size={13} />}
            {mutation.data.ok ? "Chain valid" : "Chain broken"}
          </p>
          {verifyChainEvidence(mutation.data)}
        </div>
      )}

      {mutation.isError && <DemoRunError error={mutation.error} />}
    </DemoCardShell>
  );
}

export function DemoLabSection() {
  const health = useMerchantHealth();
  const [dismissedGuardNotice, setDismissedGuardNotice] = useState(false);

  const notSimulator = health.data ? health.data.payment_mode !== "simulator" : false;
  const disabledReason = notSimulator
    ? "Demo Lab requires PAYMENT_PROVIDER=simulator in this environment."
    : null;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-semibold text-navy-900">
          <FlaskConical className="text-coral-600" size={22} />
          Demo Lab
        </h1>
        <p className="mt-1 text-sm text-navy-500">
          Safe sandbox for a live demonstration -- isolated, guarded, never touches real inventory
          or payments.
        </p>
      </div>

      {notSimulator && !dismissedGuardNotice && (
        <div className="flex items-start justify-between gap-3 rounded-2xl border border-coral-100 bg-coral-100/40 p-4 text-sm text-navy-900">
          <p>
            This environment is not configured local/simulator-safe, so Demo Lab actions are
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

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <ScenarioCard
          title="Stale price"
          description="See how an expired price lock is detected and safely re-quoted."
          icon={ShieldCheck}
          endpoint="stale-price"
          disabledReason={disabledReason}
        />
        <ScenarioCard
          title="Payment decline"
          description="Simulate a declined payment and watch the compensation/release path."
          icon={CreditCard}
          endpoint="payment-decline"
          disabledReason={disabledReason}
        />
        <ScenarioCard
          title="LLM unavailable"
          description="See the deterministic fallback path when the LLM can't be reached."
          icon={Cloud}
          endpoint="llm-unavailable"
          disabledReason={disabledReason}
        />
        <VerifyChainCard disabledReason={disabledReason} />
      </div>
    </div>
  );
}
