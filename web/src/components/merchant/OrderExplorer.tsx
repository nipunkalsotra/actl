import { motion } from "framer-motion";
import {
  ArrowLeft,
  BadgeCheck,
  CheckCircle2,
  CircleAlert,
  Clock,
  ExternalLink,
  ShieldCheck,
  X,
} from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useOrderStatus } from "../../api/hooks";
import { useMerchantOrderAudit, useMerchantOrders } from "../../api/merchantHooks";
import type { MerchantOrderAudit } from "../../api/merchantTypes";
import { describeAnchorStatus } from "../../lib/anchorStatus";
import { formatMinor } from "../../lib/money";
import { Overlay } from "../Overlay";

type StepStatus = "verified" | "failed" | "pending" | "not_tracked";

interface Step {
  label: string;
  status: StepStatus;
  detail?: string;
}

function buildSteps(audit: MerchantOrderAudit): Step[] {
  const byAction = (action: string) => audit.timeline.find((t) => t.action === action);
  const mandateLocked = byAction("mandate.locked");
  const quoteIssued = byAction("quote.issued");
  const orderProposed = byAction("order.proposed");
  const settlementClosed = byAction("settlement.closed");
  const compensationApplied = byAction("compensation.applied");
  const isCaptured = audit.terminal_outcome.status === "CAPTURED";
  const isCompensated = audit.terminal_outcome.status === "COMPENSATED" || compensationApplied !== undefined;

  return [
    { label: "Mandate locked", status: mandateLocked ? "verified" : "pending" },
    {
      label: "Catalog filtered",
      status: "not_tracked",
      detail: "Not linked to a specific order in this build's audit schema",
    },
    { label: "Quote issued", status: quoteIssued ? "verified" : "pending" },
    { label: "Seven money gates passed", status: orderProposed ? "verified" : "pending" },
    {
      label: "Payment captured",
      status: isCaptured ? "verified" : "failed",
      detail: isCaptured ? undefined : `Terminal: ${audit.terminal_outcome.status}`,
    },
    {
      label: "Ledger settled",
      status: settlementClosed ? "verified" : isCompensated ? "failed" : "pending",
      detail: isCompensated ? "Compensated -- reservation released" : undefined,
    },
    {
      label: "Audit chain verified",
      status:
        audit.chain_verified === true ? "verified" : audit.chain_verified === false ? "failed" : "pending",
    },
    {
      label: "Monad Testnet anchored",
      status:
        audit.anchor?.status === "anchored"
          ? "verified"
          : audit.anchor?.status === "conflict"
            ? "failed"
            : "not_tracked",
      detail: audit.anchor?.status === "anchored" ? undefined : describeAnchorStatus(audit.anchor).headline,
    },
  ];
}

const STEP_ICON: Record<StepStatus, typeof CheckCircle2> = {
  verified: CheckCircle2,
  failed: CircleAlert,
  pending: Clock,
  not_tracked: Clock,
};

const STEP_TONE: Record<StepStatus, string> = {
  verified: "bg-emerald-100 text-emerald-600",
  failed: "bg-coral-100 text-coral-600",
  pending: "bg-sky-100 text-navy-500",
  not_tracked: "bg-sky-100 text-navy-500",
};

interface OrderExplorerProps {
  orderId: string | null;
  onClose: () => void;
  showBackToBuyer?: boolean;
}

export function OrderExplorer({ orderId, onClose, showBackToBuyer = false }: OrderExplorerProps) {
  const orders = useMerchantOrders(100);
  const status = useOrderStatus(orderId);
  const audit = useMerchantOrderAudit(orderId);
  const [showRawEvidence, setShowRawEvidence] = useState(false);
  const navigate = useNavigate();

  const orderSummary = orders.data?.items.find((o) => o.order_id === orderId);

  return (
    <Overlay open={orderId !== null} onClose={onClose}>
      <motion.div
        role="dialog"
        aria-modal="true"
        aria-label="Order Explorer"
        initial={{ x: "100%" }}
        animate={{ x: 0 }}
        exit={{ x: "100%" }}
        transition={{ type: "spring", stiffness: 320, damping: 32 }}
        className="fixed inset-y-0 right-0 z-50 flex w-full max-w-lg flex-col overflow-hidden bg-white shadow-float sm:rounded-l-3xl"
      >
        <div className="flex items-start justify-between border-b border-sky-100 px-5 py-4">
          <div className="min-w-0">
            {showBackToBuyer && (
              <button
                type="button"
                onClick={() => navigate("/")}
                className="mb-1 flex items-center gap-1 text-xs font-medium text-ocean-600 hover:underline"
              >
                <ArrowLeft size={12} /> Back to buyer
              </button>
            )}
            <h2 className="text-base font-semibold text-navy-900">Order Explorer</h2>
            {orderSummary && (
              <p className="mt-0.5 truncate text-sm text-navy-500">{orderSummary.sku ?? "—"}</p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close order explorer"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-navy-500 hover:bg-sky-50"
          >
            <X size={16} />
          </button>
        </div>

        {status.data && (
          <div className="border-b border-sky-100 bg-sky-50 px-5 py-3">
            <div className="flex items-center justify-between">
              <p className="text-lg font-semibold text-navy-900">{formatMinor(status.data.amount_minor)}</p>
              <span
                className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
                  status.data.status === "CAPTURED"
                    ? "bg-emerald-100 text-emerald-600"
                    : "bg-coral-100 text-coral-600"
                }`}
              >
                {status.data.status}
              </span>
            </div>
            <p className="mt-0.5 font-mono text-xs text-navy-500">{status.data.order_id}</p>
          </div>
        )}

        <div className="flex-1 overflow-y-auto p-5">
          {(status.isLoading || audit.isLoading) && <p className="text-sm text-navy-500">Loading…</p>}
          {(status.isError || audit.isError) && (
            <p className="text-sm text-coral-600">Couldn't load this order's evidence.</p>
          )}

          {audit.data && (
            <>
              <ol className="space-y-3">
                {buildSteps(audit.data).map((step, i) => {
                  const Icon = STEP_ICON[step.status];
                  return (
                    <motion.li
                      key={step.label}
                      initial={{ opacity: 0, x: -8 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ delay: i * 0.05 }}
                      className="flex items-start gap-3"
                    >
                      <span
                        className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${STEP_TONE[step.status]}`}
                      >
                        <Icon size={15} />
                      </span>
                      <div>
                        <p className="text-sm font-medium text-navy-900">{step.label}</p>
                        {step.detail && <p className="text-xs text-navy-500">{step.detail}</p>}
                      </div>
                    </motion.li>
                  );
                })}
              </ol>

              <button
                type="button"
                onClick={() => setShowRawEvidence((v) => !v)}
                className="mt-4 text-xs font-medium text-ocean-600 hover:underline"
              >
                {showRawEvidence ? "Hide" : "Show"} detailed evidence
              </button>

              {showRawEvidence && (
                <div className="mt-3 space-y-2">
                  {audit.data.timeline.map((item, idx) => (
                    <details
                      key={`${item.action}-${idx}`}
                      className="rounded-xl border border-sky-100 bg-sky-50 p-3 text-sm"
                    >
                      <summary className="cursor-pointer font-medium text-navy-900">
                        {item.action} {item.seq !== null && `(seq ${item.seq})`}
                      </summary>
                      <div className="mt-2 space-y-1 text-xs text-navy-500">
                        {item.hashes.entry_hash && (
                          <p className="break-all">hash {item.hashes.entry_hash}</p>
                        )}
                      </div>
                    </details>
                  ))}
                </div>
              )}

              <div className="my-5 h-px bg-sky-100" />

              <div className="flex flex-col gap-2">
                {audit.data.anchor?.status === "anchored" && audit.data.anchor.explorer_url ? (
                  <a
                    href={audit.data.anchor.explorer_url}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center justify-center gap-2 rounded-xl bg-ocean-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-ocean-500"
                  >
                    <BadgeCheck size={15} />
                    View Monad proof
                    <ExternalLink size={13} />
                  </a>
                ) : (
                  (() => {
                    const anchorDesc = describeAnchorStatus(audit.data.anchor);
                    return (
                      <p
                        className={`rounded-xl px-4 py-2.5 text-center text-xs ${
                          anchorDesc.tone === "conflict"
                            ? "bg-coral-100 text-coral-700"
                            : "bg-sky-50 text-navy-500"
                        }`}
                      >
                        {anchorDesc.tone === "conflict" && (
                          <CircleAlert size={13} className="mr-1 inline" />
                        )}
                        {anchorDesc.headline}
                        {anchorDesc.detail ? ` — ${anchorDesc.detail}` : ""}
                      </p>
                    );
                  })()
                )}
              </div>

              <p className="mt-4 flex items-center gap-1.5 text-xs text-navy-500">
                <ShieldCheck size={13} className="text-emerald-600" />
                All steps verifiable. No data exposed to buyers.
              </p>
            </>
          )}
        </div>
      </motion.div>
    </Overlay>
  );
}
