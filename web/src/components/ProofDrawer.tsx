import { motion } from "framer-motion";
import { BadgeCheck, ExternalLink, ShieldCheck, X } from "lucide-react";
import { useAuditExplain } from "../api/hooks";
import { useJourney } from "../state/journeyContext";
import type { TimelineItem } from "../api/types";
import { Overlay } from "./Overlay";

const STAGE_BY_ACTION: Record<string, string> = {
  "mandate.locked": "Mandate",
  "mandate.executing": "Mandate",
  "mandate.revoked": "Mandate",
  "quote.issued": "Quote",
  "order.proposed": "Safety checks",
  "policy.decision": "Safety checks",
  "payment.intent": "Payment",
  "payment.result": "Payment",
  "webhook.received": "Payment",
  "compensation.applied": "Payment",
  "budget.reserved": "Ledger",
  "settlement.closed": "Ledger",
  "reservation.released": "Ledger",
  "reservation.expired": "Ledger",
};

const STAGE_ORDER = ["Mandate", "Quote", "Safety checks", "Payment", "Ledger"];

function friendlyAction(action: string): string {
  return action
    .split(".")
    .join(" ")
    .replace(/^\w/, (c) => c.toUpperCase());
}

export function ProofDrawer() {
  const { proofOrderId, setProofOrderId } = useJourney();
  const explain = useAuditExplain(proofOrderId);

  const grouped: Record<string, TimelineItem[]> = {};
  for (const item of explain.data?.timeline ?? []) {
    const stage = STAGE_BY_ACTION[item.action] ?? "Other";
    (grouped[stage] ??= []).push(item);
  }

  return (
    <Overlay open={proofOrderId !== null} onClose={() => setProofOrderId(null)}>
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-label="Audit proof"
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", stiffness: 320, damping: 32 }}
            className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col overflow-hidden bg-white shadow-float sm:rounded-l-3xl"
          >
            <div className="flex items-center justify-between border-b border-sky-100 px-5 py-4">
              <h2 className="flex items-center gap-2 text-base font-semibold text-navy-900">
                <ShieldCheck size={18} className="text-emerald-600" />
                Audit proof
              </h2>
              <button
                type="button"
                onClick={() => setProofOrderId(null)}
                aria-label="Close proof"
                className="flex h-8 w-8 items-center justify-center rounded-full text-navy-500 hover:bg-sky-50"
              >
                <X size={16} />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-5">
              {explain.isLoading && <p className="text-sm text-navy-500">Loading audit trail…</p>}
              {explain.isError && <p className="text-sm text-coral-600">Couldn't load audit proof.</p>}

              {explain.data && (
                <>
                  <div className="mb-4 rounded-xl bg-emerald-100 px-3 py-2.5 text-sm font-medium text-emerald-600">
                    Terminal outcome: {explain.data.terminal_outcome.status}
                  </div>

                  <ol className="space-y-4">
                    {STAGE_ORDER.filter((stage) => grouped[stage]?.length).map((stage) => (
                      <li key={stage}>
                        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-navy-500">
                          {stage}
                        </p>
                        <div className="space-y-2">
                          {grouped[stage].map((item, idx) => (
                            <details
                              key={`${item.action}-${idx}`}
                              className="rounded-xl border border-sky-100 bg-sky-50 p-3 text-sm"
                            >
                              <summary className="cursor-pointer font-medium text-navy-900">
                                {friendlyAction(item.action)}
                              </summary>
                              <div className="mt-2 space-y-1 text-xs text-navy-500">
                                {item.seq !== null && <p>seq {item.seq}</p>}
                                {item.trace_id && <p className="break-all">trace {item.trace_id}</p>}
                                {item.hashes.entry_hash && (
                                  <p className="break-all">hash {item.hashes.entry_hash}</p>
                                )}
                              </div>
                            </details>
                          ))}
                        </div>
                      </li>
                    ))}
                  </ol>

                  <div className="my-5 h-px bg-sky-100" />

                  <div>
                    <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-navy-500">
                      Audit proof
                    </p>
                    {explain.data.anchor && explain.data.anchor.status === "anchored" ? (
                      <div className="rounded-xl border border-emerald-100 bg-emerald-100/50 p-3 text-sm">
                        <p className="flex items-center gap-1.5 font-medium text-emerald-600">
                          <BadgeCheck size={15} /> Anchored on Monad Testnet
                        </p>
                        <p className="mt-1 text-xs text-navy-500">
                          Checkpoint seq {explain.data.anchor.checkpoint_range.from_seq}–
                          {explain.data.anchor.checkpoint_range.to_seq}
                        </p>
                        {explain.data.anchor.explorer_url && (
                          <a
                            href={explain.data.anchor.explorer_url}
                            target="_blank"
                            rel="noreferrer"
                            className="mt-2 flex items-center gap-1 text-xs font-medium text-ocean-600 hover:underline"
                          >
                            View on Monad explorer <ExternalLink size={12} />
                          </a>
                        )}
                      </div>
                    ) : (
                      <p className="rounded-xl bg-sky-50 p-3 text-xs text-navy-500">
                        This order's checkpoint hasn't been anchored to Monad Testnet yet (anchoring is
                        optional and asynchronous — the offline-verifiable hash chain above is the
                        primary proof regardless).
                      </p>
                    )}
                  </div>
                </>
              )}
            </div>
          </motion.div>
    </Overlay>
  );
}
