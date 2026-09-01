import type { MandateResponse } from "../api/types";

/** Client-side early UX guard only: real catalog price x current trip
 * nights against the locked mandate's own total cap. This never
 * authorizes or blocks a purchase -- the server-side quote/policy/money
 * gates (application/gate.py G1-G7) remain the only authoritative check,
 * unchanged. Before a mandate exists there's no total cap to guard
 * against, so every item is considered within budget. */
export function isWithinTripBudget(
  unitPriceMinor: number,
  nights: number,
  mandate: MandateResponse | null,
): boolean {
  if (!mandate) return true;
  return unitPriceMinor * nights <= mandate.bounds.max_total_minor;
}
