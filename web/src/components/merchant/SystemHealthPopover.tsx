import { Activity, CircleAlert, CircleCheck } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useMerchantHealth } from "../../api/merchantHooks";

const ROWS: { key: "api" | "database" | "redis" | "audit_chain"; label: string }[] = [
  { key: "api", label: "API" },
  { key: "database", label: "Database" },
  { key: "redis", label: "Redis" },
  { key: "audit_chain", label: "Audit chain" },
];

export function SystemHealthPopover() {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const health = useMerchantHealth();

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    const onClickAway = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("mousedown", onClickAway);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("mousedown", onClickAway);
    };
  }, [open]);

  const allOk = health.data
    ? ROWS.every((row) => health.data![row.key] === "ok")
    : undefined;

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="dialog"
        aria-expanded={open}
        className={`flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-ocean-500 ${
          allOk === undefined
            ? "bg-sky-100 text-navy-500"
            : allOk
              ? "bg-emerald-100 text-emerald-600"
              : "bg-coral-100 text-coral-600"
        }`}
      >
        <span
          className={`h-2 w-2 rounded-full ${
            allOk === undefined ? "bg-navy-500" : allOk ? "bg-emerald-500" : "bg-coral-500"
          }`}
        />
        {allOk === undefined ? "Checking systems…" : allOk ? "All systems healthy" : "Attention needed"}
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="System health"
          className="absolute left-0 z-40 mt-2 w-72 rounded-2xl border border-sky-100 bg-card p-4 shadow-card sm:left-auto sm:right-0"
        >
          <p className="mb-3 flex items-center gap-1.5 text-sm font-semibold text-navy-900">
            <Activity size={15} /> System health
          </p>
          {health.isLoading && <p className="text-sm text-navy-500">Loading…</p>}
          {health.isError && <p className="text-sm text-coral-400">Couldn't load health status.</p>}
          {health.data && (
            <dl className="space-y-2">
              {ROWS.map((row) => (
                <div key={row.key} className="flex items-center justify-between text-sm">
                  <dt className="text-navy-500">{row.label}</dt>
                  <dd
                    className={`flex items-center gap-1 font-medium ${
                      health.data![row.key] === "ok" ? "text-emerald-600" : "text-coral-400"
                    }`}
                  >
                    {health.data![row.key] === "ok" ? (
                      <CircleCheck size={13} />
                    ) : (
                      <CircleAlert size={13} />
                    )}
                    {health.data![row.key]}
                  </dd>
                </div>
              ))}
              <div className="my-1 h-px bg-sky-100" />
              <div className="flex items-center justify-between text-sm">
                <dt className="text-navy-500">Payment mode</dt>
                <dd className="font-medium text-navy-900">{health.data.payment_mode}</dd>
              </div>
              <div className="flex items-center justify-between text-sm">
                <dt className="text-navy-500">Anchor mode</dt>
                <dd className="font-medium text-navy-900">{health.data.anchor_mode}</dd>
              </div>
            </dl>
          )}
        </div>
      )}
    </div>
  );
}
