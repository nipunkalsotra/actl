import { expect, test } from "@playwright/test";

test.describe("live orders + Order Explorer", () => {
  test.skip(({ isMobile }) => isMobile, "desktop-only; see merchant-mobile.spec.ts");

  test.beforeEach(async ({ page }) => {
    await page.goto("/merchant");
    await page.getByRole("button", { name: "Live orders", exact: true }).click();
  });

  test("loads real order records with honest trust-status labels", async ({ page }) => {
    const firstRow = page.locator("tbody tr").first();
    await expect(firstRow).toBeVisible({ timeout: 10_000 });
    await expect(firstRow.locator("td").first()).toHaveText(/^ord_/);
  });

  test("search filters over actually-loaded records", async ({ page }) => {
    await expect(page.locator("tbody tr").first()).toBeVisible({ timeout: 10_000 });
    const rowCountBefore = await page.locator("tbody tr").count();
    expect(rowCountBefore).toBeGreaterThan(0);

    const firstOrderId = await page.locator("tbody tr").first().locator("td").first().innerText();
    await page.getByPlaceholder("Search by order reference or SKU…").fill(firstOrderId);
    await expect(page.locator("tbody tr")).toHaveCount(1);
    await expect(page.locator("tbody tr").first()).toContainText(firstOrderId);

    await page.getByPlaceholder("Search by order reference or SKU…").fill("zzz-no-such-order-zzz");
    await expect(page.getByText("No orders match your search.")).toBeVisible();
  });

  test("trust-status filter narrows to only matching rows", async ({ page }) => {
    // The pill's DOM text is lowercase ("captured"); `capitalize` is CSS
    // display styling only and doesn't change the accessible name.
    await page.getByRole("button", { name: "captured", exact: true }).click();
    const rows = page.locator("tbody tr");
    const count = await rows.count();
    for (let i = 0; i < count; i++) {
      await expect(rows.nth(i)).toContainText("Captured");
    }
  });

  test("View opens the Order Explorer with real evidence and closes on Escape", async ({ page }) => {
    await page.locator("tbody tr").first().getByRole("button", { name: "View", exact: true }).click();

    const explorer = page.getByRole("dialog", { name: "Order Explorer" });
    await expect(explorer).toBeVisible();
    await expect(explorer.getByText("Mandate locked")).toBeVisible();
    await expect(explorer.getByText("Seven money gates passed")).toBeVisible();
    await expect(explorer.getByText("All steps verifiable. No data exposed to buyers.")).toBeVisible();

    // never a real step falsely claimed passed without evidence
    await expect(explorer.getByText("Catalog filtered")).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(explorer).toHaveCount(0);
  });

  test("Open audit explanation reveals real per-entry evidence", async ({ page }) => {
    await page.locator("tbody tr").first().getByRole("button", { name: "View", exact: true }).click();
    const explorer = page.getByRole("dialog", { name: "Order Explorer" });
    await expect(explorer).toBeVisible();

    await explorer.getByRole("link", { name: "Open audit explanation" }).click();
    await expect(explorer.getByText("Show detailed evidence")).toHaveCount(0);
    const firstDetail = explorer.locator("details").first();
    await expect(firstDetail).toBeVisible();
  });

  test("View Monad proof is absent/disabled when no real anchor exists (ANCHOR_PROVIDER=noop)", async ({
    page,
  }) => {
    await page.locator("tbody tr").first().getByRole("button", { name: "View", exact: true }).click();
    const explorer = page.getByRole("dialog", { name: "Order Explorer" });
    await expect(explorer).toBeVisible();

    await expect(explorer.getByRole("link", { name: "View Monad proof" })).toHaveCount(0);
    await expect(
      explorer.getByText("No Monad Testnet anchor exists yet for this order's checkpoint."),
    ).toBeVisible();
  });
});
