import { expect, test } from "@playwright/test";

test.describe("buyer <-> merchant navigation", () => {
  test.skip(({ isMobile }) => isMobile, "desktop-only; mobile nav covered by merchant-mobile.spec.ts");

  test("buyer page remains reachable and working after the merchant phase", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Stays in Goa" })).toBeVisible();
    await expect(page.getByTestId("hotel-card-HTL-GOA-BUDGET-RM")).toBeVisible();
  });

  test("Merchant view link navigates from buyer to merchant and back", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("link", { name: "Merchant view" }).click();
    await expect(page).toHaveURL(/\/merchant$/);
    await expect(page.getByRole("heading", { name: "Merchant Control Center" })).toBeVisible();

    await page.getByRole("link", { name: "Buyer experience" }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("heading", { name: "Stays in Goa" })).toBeVisible();
  });

  test("sidebar switches the active section and visibly changes content", async ({ page }) => {
    await page.goto("/merchant");
    await expect(page.getByRole("heading", { name: "Growth, within customer consent." })).toBeVisible();

    await page.getByRole("button", { name: "Live orders", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Live orders", exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Growth, within customer consent." })).toHaveCount(0);

    await page.getByRole("button", { name: "Catalog", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Catalog", exact: true })).toBeVisible();

    await page.getByRole("button", { name: "Trust & audit", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Trust & audit", exact: true })).toBeVisible();

    await page.getByRole("button", { name: "Demo Lab", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Demo Lab", exact: true })).toBeVisible();
  });
});
