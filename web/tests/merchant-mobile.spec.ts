import { expect, test } from "@playwright/test";

test.describe("merchant: mobile layout", () => {
  test.skip(({ isMobile }) => !isMobile, "mobile-viewport-only checks");

  test("hamburger opens a navigation drawer that switches sections", async ({ page }) => {
    await page.goto("/merchant");
    await expect(page.getByRole("button", { name: "Open navigation" })).toBeVisible({ timeout: 10_000 });
    await page.getByRole("button", { name: "Open navigation" }).click();

    const nav = page.getByRole("dialog", { name: "Navigation" });
    await expect(nav).toBeVisible();
    await nav.getByRole("button", { name: "Live orders", exact: true }).click();
    await expect(nav).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Live orders", exact: true })).toBeVisible();
  });

  test("Order Explorer becomes a near full-height sheet on mobile", async ({ page }) => {
    await page.goto("/merchant");
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("dialog", { name: "Navigation" }).getByRole("button", { name: "Live orders", exact: true }).click();

    const firstRow = page.locator("tbody tr").first();
    await expect(firstRow).toBeVisible({ timeout: 10_000 });
    await firstRow.getByRole("button", { name: "View", exact: true }).click();

    const explorer = page.getByRole("dialog", { name: "Order Explorer" });
    await expect(explorer).toBeVisible();
    const box = await explorer.boundingBox();
    const viewport = page.viewportSize();
    expect(box).not.toBeNull();
    expect(viewport).not.toBeNull();
    expect(box!.height).toBeGreaterThan(viewport!.height * 0.6);
  });
});
