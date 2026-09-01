import { expect, test } from "@playwright/test";

test.describe("filters and sort update real catalog results", () => {
  // Desktop-only: on mobile, filters live inside the bottom sheet
  // (covered by mobile.spec.ts) rather than the always-visible sidebar.
  test.skip(({ isMobile }) => isMobile, "desktop-only; see mobile.spec.ts");

  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Stays in Goa" })).toBeVisible();
  });

  test("refundable-only filter changes the result count", async ({ page }) => {
    const countText = page.getByText(/stays match your preferences/);
    await expect(countText).toBeVisible();
    const withRefundableOnly = await countText.textContent();

    await page.getByLabel("Refundable only").uncheck();
    await expect(countText).not.toHaveText(withRefundableOnly ?? "");
  });

  test("minimum rating filter narrows results deterministically", async ({ page }) => {
    // HTL-GOA-BUDGET-RM has rating 3.5 -- must disappear once 4+ is applied.
    await expect(page.getByTestId("hotel-card-HTL-GOA-BUDGET-RM")).toBeVisible();
    await page.getByRole("button", { name: "4+" }).click();
    await expect(page.getByTestId("hotel-card-HTL-GOA-BUDGET-RM")).toHaveCount(0);
  });

  test("price low to high sort orders real fetched prices ascending", async ({ page }) => {
    await page.getByLabel("Refundable only").uncheck();
    await page.getByRole("button", { name: "Price low to high" }).click();

    const prices = await page.locator('[data-testid^="hotel-card-"]').evaluateAll((cards) =>
      cards.map((card) => {
        const text = card.querySelector("p.text-base")?.textContent ?? "";
        return Number(text.replace(/[^\d]/g, ""));
      }),
    );
    const sorted = [...prices].sort((a, b) => a - b);
    expect(prices).toEqual(sorted);
  });

  test("best match is disabled until a mandate is locked", async ({ page }) => {
    const bestMatch = page.getByRole("button", { name: "Best match" });
    await expect(bestMatch).toBeDisabled();
  });
});
