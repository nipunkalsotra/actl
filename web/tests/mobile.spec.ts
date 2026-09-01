import { expect, test } from "@playwright/test";

test.describe("mobile layout", () => {
  test.skip(({ isMobile }) => !isMobile, "mobile-viewport-only checks");

  test("filters collapse into a bottom sheet and the full flow still works", async ({ page }) => {
    await page.goto("/");

    // The desktop sticky sidebar is hidden; a Filters trigger opens a sheet instead.
    const filtersButton = page.getByRole("button", { name: "Filters" });
    await expect(filtersButton).toBeVisible();
    await filtersButton.click();
    await expect(page.getByRole("dialog", { name: "Filters" })).toBeVisible();
    await page.getByLabel("Refundable only").uncheck();
    await page.getByRole("button", { name: "Close filters" }).click();
    await expect(page.getByRole("dialog", { name: "Filters" })).toHaveCount(0);

    await page.getByTestId("hotel-select-HTL-GOA-BUDGET-RM").click();
    await expect(page.getByText("Selected: Budget Room")).toBeVisible();
  });

  test("chat opens as a near full-height sheet on mobile", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Open ACTL travel assistant" }).click();
    const panel = page.getByRole("complementary", { name: "ACTL travel assistant" });
    await expect(panel).toBeVisible();
    const box = await panel.boundingBox();
    const viewport = page.viewportSize();
    expect(box).not.toBeNull();
    expect(viewport).not.toBeNull();
    // "full-height sheet" -- most of the viewport height, not a small widget.
    expect(box!.height).toBeGreaterThan(viewport!.height * 0.6);
  });
});
