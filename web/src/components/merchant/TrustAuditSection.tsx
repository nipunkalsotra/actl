import { BadgeCheck, CircleAlert, ExternalLink, Link2, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { useMerchantTrust } from "../../api/merchantHooks";
import { describeAnchorStatus } from "../../lib/anchorStatus";

const GUARANTEES = [
  {
    title: "Quote freshness protection",
    body: "Every quote pins a catalog version and expiry. A price mutated or a quote expired after issuance is rejected at the gate before any charge, never silently honoured.",
  },
  {
    title: "Capture-after-signature protection",
    body: "Payment capture is unreachable unless the checkout signature verifies. A tampered or invalid signature yields a declined, compensated saga -- never a charge.",
  },
  {
    title: "Ledger reservation/settlement health",
    body: "Reservations and settlements are double-entry, idempotent by mandate + intent hash. A failed settlement after provider capture triggers automatic refund and reversal, never an orphaned charge.",
  },
];

export function TrustAuditSection() {
  const trust = useMerchantTrust();
  const [showRaw, setShowRaw] = useState(false);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-navy-900">Trust & audit</h1>
        <p className="mt-1 text-sm text-navy-500">
          Operational status of the append-only, hash-chained audit trail.
        </p>
      </div>

      {trust.isLoading && <div className="h-32 animate-pulse rounded-2xl bg-white shadow-card" />}
      {trust.isError && (
        <div className="rounded-2xl border border-coral-100 bg-coral-100/40 p-6 text-center text-sm text-navy-700">
          Couldn't load trust status.
        </div>
      )}

      {trust.data && (
        <div className="rounded-2xl border border-sky-100 bg-white p-5 shadow-card">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="flex items-center gap-2 text-sm font-semibold text-emerald-600">
              <ShieldCheck size={16} />
              Audit chain reachable
            </p>
            <button
              type="button"
              onClick={() => setShowRaw((v) => !v)}
              className="text-xs font-medium text-ocean-600 hover:underline"
            >
              {showRaw ? "Hide" : "Show"} sequence & hash details
            </button>
          </div>

          <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-3">
            <div>
              <p className="text-xs text-navy-500">Chain length</p>
              <p className="text-lg font-semibold text-navy-900">
                {trust.data.chain_head_seq ?? "—"} entries
              </p>
            </div>
            <div>
              <p className="text-xs text-navy-500">Checkpoints</p>
              <p className="text-lg font-semibold text-navy-900">{trust.data.checkpoint_count}</p>
            </div>
            <div>
              <p className="text-xs text-navy-500">Anchor mode</p>
              <p className="text-lg font-semibold text-navy-900">{trust.data.anchor_provider}</p>
            </div>
          </div>

          {showRaw && (
            <div className="mt-4 space-y-1 rounded-xl bg-sky-50 p-3 text-xs text-navy-500">
              <p className="break-all">head hash: {trust.data.chain_head_hash ?? "—"}</p>
              {trust.data.latest_checkpoint && (
                <p className="break-all">
                  latest checkpoint: seq {trust.data.latest_checkpoint.from_seq}–
                  {trust.data.latest_checkpoint.to_seq} root {trust.data.latest_checkpoint.merkle_root}
                </p>
              )}
            </div>
          )}

          <div className="my-4 h-px bg-sky-100" />

          {trust.data.latest_checkpoint?.anchor_status === "anchored" &&
          trust.data.latest_checkpoint.explorer_url ? (
            <a
              href={trust.data.latest_checkpoint.explorer_url}
              target="_blank"
              rel="noreferrer"
              className="flex w-fit items-center gap-1.5 rounded-full bg-ocean-600 px-4 py-2 text-sm font-medium text-white hover:bg-ocean-500"
            >
              <BadgeCheck size={15} />
              View latest Monad Testnet anchor
              <ExternalLink size={13} />
            </a>
          ) : (
            (() => {
              const anchorDesc = describeAnchorStatus(
                trust.data.latest_checkpoint
                  ? { status: trust.data.latest_checkpoint.anchor_status }
                  : null,
              );
              return (
                <p
                  className={`flex items-center gap-1.5 text-xs ${
                    anchorDesc.tone === "conflict" ? "text-coral-700" : "text-navy-500"
                  }`}
                >
                  {anchorDesc.tone === "conflict" ? <CircleAlert size={13} /> : <Link2 size={13} />}
                  {anchorDesc.headline}
                  {anchorDesc.detail ? ` — ${anchorDesc.detail}` : ""}
                </p>
              );
            })()
          )}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {GUARANTEES.map((g) => (
          <div key={g.title} className="rounded-2xl border border-sky-100 bg-white p-5 shadow-card">
            <p className="flex items-center gap-2 text-sm font-semibold text-navy-900">
              <ShieldCheck size={15} className="text-emerald-600" />
              {g.title}
            </p>
            <p className="mt-2 text-xs text-navy-500">{g.body}</p>
          </div>
        ))}
      </div>

      <p className="text-xs text-navy-500">
        Reconciliation state is not exposed as a live status in this build.
      </p>
    </div>
  );
}
