// Static, purely cosmetic per-SKU presentation (display name + card
// gradient) -- every commercial fact (price, rating, refundability,
// availability) still comes only from the real backend catalog response.

const FRIENDLY_NAMES: Record<string, string> = {
  "HTL-GOA-SEA-DLX": "Sea View Deluxe",
  "HTL-GOA-SUNSET-STD": "Sunset Standard",
  "HTL-GOA-PALM-STE": "Palm Suite",
  "HTL-GOA-BUDGET-RM": "Budget Room",
  "HTL-GOA-CLIFF-VIL": "Cliffside Villa",
  "HTL-GOA-SOLDOUT-RM": "Lagoon Room",
};

export function hotelDisplayName(sku: string): string {
  if (FRIENDLY_NAMES[sku]) return FRIENDLY_NAMES[sku];
  return sku
    .replace(/^HTL-/, "")
    .split("-")
    .map((word) => word.charAt(0) + word.slice(1).toLowerCase())
    .join(" ");
}

const GRADIENTS = [
  "from-ocean-500 to-sky-100",
  "from-emerald-500 to-sky-100",
  "from-coral-500 to-sky-100",
  "from-navy-500 to-ocean-100",
];

function hashSku(sku: string): number {
  let hash = 0;
  for (let i = 0; i < sku.length; i++) hash = (hash * 31 + sku.charCodeAt(i)) >>> 0;
  return hash;
}

export function hotelGradient(sku: string): string {
  return GRADIENTS[hashSku(sku) % GRADIENTS.length];
}
