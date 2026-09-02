import { expect, test } from "@playwright/test";

test.describe("merchant: every visible control does something real", () => {
  test.skip(({ isMobile }) => isMobile, "desktop-only; see merchant-mobile.spec.ts");

  test("catalog section loads real inventory with no price-mutation controls", async ({ page }) => {
    await page.goto("/merchant");
    await page.getByRole("button", { name: "Catalog", exact: true }).click();
    const firstRow = page.locator("tbody tr").first();
    await expect(firstRow).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole("button", { name: /mutate|edit price|set price/i })).toHaveCount(0);
  });

  test("trust & audit details toggle reveals real sequence/hash evidence", async ({ page }) => {
    await page.goto("/merchant");
    await page.getByRole("button", { name: "Trust & audit", exact: true }).click();
    await expect(page.getByText("Audit chain reachable")).toBeVisible({ timeout: 10_000 });

    await page.getByRole("button", { name: "Show sequence & hash details" }).click();
    await expect(page.getByText(/head hash:/)).toBeVisible();
    await page.getByRole("button", { name: "Hide sequence & hash details" }).click();
    await expect(page.getByText(/head hash:/)).toHaveCount(0);
  });

  test("Monad Testnet link is absent when no checkpoint is anchored yet", async ({ page }) => {
    await page.goto("/merchant");
    await page.getByRole("button", { name: "Trust & audit", exact: true }).click();
    await expect(page.getByText("Audit chain reachable")).toBeVisible({ timeout: 10_000 });
    // Two honest, distinct "not anchored" states -- no checkpoint exists
    // yet at all, or one exists but has no on-chain tx (ANCHOR_PROVIDER=
    // noop) -- either way, never a false "anchored" claim.
    await expect(
      page
        .getByText("Awaiting the next audit checkpoint")
        .or(page.getByText(/hasn't been anchored to Monad Testnet yet/)),
    ).toBeVisible();
    await expect(page.getByText("View latest Monad Testnet anchor")).toHaveCount(0);
  });

  test("profile control and destination chip are present and non-decorative header elements load", async ({
    page,
  }) => {
    await page.goto("/merchant");
    await expect(page.getByText(/^Demo run: /)).toBeVisible();
    await expect(page.getByRole("link", { name: "Buyer experience" })).toBeVisible();
  });
});
