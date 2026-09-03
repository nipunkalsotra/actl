import { Lock, MessageCircleMore } from "lucide-react";
import { formatMinor } from "../lib/money";
import { useJourney } from "../state/journeyContext";

interface MandateCardProps {
  onOpenChat: () => void;
}

export function MandateCard({ onOpenChat }: MandateCardProps) {
  const { filters, mandate, setMandate, resetForNewBrowse } = useJourney();

  const totalMinor = mandate ? mandate.bounds.max_total_minor : filters.budgetMaxMinor;
  const unitMinor = mandate ? mandate.bounds.max_unit_minor : Math.floor(filters.budgetMaxMinor / filters.nights);
  const requireRefundable = mandate ? mandate.bounds.require_refundable : filters.refundableOnly;

  const handleRevise = () => {
    const confirmed = window.confirm(
      "Start a fresh mandate? Your currently locked mandate stays valid for any purchase already made under it, but you'll need to lock a new one to buy anything else.",
    );
    if (!confirmed) return;
    setMandate(null);
    resetForNewBrowse();
  };

  return (
    <div className="rounded-2xl border border-sky-100 bg-card p-5 shadow-card">
      <h2 className="mb-4 text-base font-semibold text-navy-900">Your travel mandate</h2>

      <dl className="space-y-2.5 text-sm">
        <div className="flex items-center justify-between">
          <dt className="text-navy-500">Total budget</dt>
          <dd className="font-medium text-navy-900">{formatMinor(totalMinor)}</dd>
        </div>
        <div className="flex items-center justify-between">
          <dt className="text-navy-500">Max/night</dt>
          <dd className="font-medium text-navy-900">{formatMinor(unitMinor)}</dd>
        </div>
        <div className="flex items-center justify-between">
          <dt className="text-navy-500">Refundable</dt>
          <dd className="font-medium text-navy-900">{requireRefundable ? "Required" : "Not required"}</dd>
        </div>
      </dl>

      <div className="my-4 h-px bg-sky-100" />

      <div className="flex items-center justify-between">
        <span className="text-sm text-navy-500">Status</span>
        {mandate ? (
          <span className="flex items-center gap-1.5 rounded-full bg-emerald-100 px-3 py-1 text-xs font-semibold text-emerald-600">
            <Lock size={12} /> Locked
          </span>
        ) : (
          <span className="rounded-full bg-sky-100 px-3 py-1 text-xs font-semibold text-navy-700">Draft</span>
        )}
      </div>

      {mandate ? (
        <button
          type="button"
          onClick={handleRevise}
          className="mt-4 w-full rounded-xl border border-sky-100 px-3 py-2 text-sm font-medium text-navy-700 hover:bg-sky-50"
        >
          Start a new mandate
        </button>
      ) : (
        <button
          type="button"
          onClick={onOpenChat}
          className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl bg-ocean-600 px-3 py-2.5 text-sm font-medium text-white hover:bg-ocean-500"
        >
          <MessageCircleMore size={16} />
          Start booking with ACTL
        </button>
      )}
    </div>
  );
}
