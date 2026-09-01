const formatter = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

export function formatMinor(amountMinor: number): string {
  return formatter.format(amountMinor / 100);
}
