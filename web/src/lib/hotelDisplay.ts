// Static, purely cosmetic per-SKU presentation (display name + card
// image/gradient) -- every commercial fact (price, rating, refundability,
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

// Illustrative/representative visuals for the six curated, persistent
// `travel.hotel` seed SKUs (scripts/seed.py) only -- never a claim about a
// real named property. HTL-DEMO-*/HTL-GROWTH-* rows (Trust Lab and growth
// simulation side effects, unbounded and not curated inventory) and any
// other unmapped SKU intentionally fall through to hotelGradient() instead.
const HOTEL_IMAGES: Record<string, string> = {
  "HTL-GOA-SEA-DLX": "/images/hotels/htl-goa-sea-dlx.webp",
  "HTL-GOA-SUNSET-STD": "/images/hotels/htl-goa-sunset-std.webp",
  "HTL-GOA-PALM-STE": "/images/hotels/htl-goa-palm-ste.webp",
  "HTL-GOA-BUDGET-RM": "/images/hotels/htl-goa-budget-rm.webp",
  "HTL-GOA-CLIFF-VIL": "/images/hotels/htl-goa-cliff-vil.webp",
  "HTL-GOA-SOLDOUT-RM": "/images/hotels/htl-goa-soldout-rm.webp",
};

export function hotelImage(sku: string): string | null {
  return HOTEL_IMAGES[sku] ?? null;
}
