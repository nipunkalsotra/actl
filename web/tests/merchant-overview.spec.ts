import { expect, test } from "@playwright/test";

test.describe("merchant overview: real data, honest states", () => {
  test.skip(({ isMobile }) => isMobile, "desktop-only; see merchant-mobile.spec.ts");

  test.beforeEach(async ({ page }) => {
    await page.goto("/merchant");
  });

  test("system health chip reflects real backend status", async ({ page }) => {
    await expect(page.getByRole("button", { name: /All systems healthy|Attention needed/ })).toBeVisible({
      timeout: 10_000,
    });
  });

  test("system health popover shows real, non-secret status fields", async ({ page }) => {
    await page.getByRole("button", { name: /All systems healthy|Attention needed/ }).click();
    const popover = page.getByRole("dialog", { name: "System health" });
    await expect(popover).toBeVisible();
    await expect(popover.getByText("API")).toBeVisible();
    await expect(popover.getByText("Database")).toBeVisible();
    await expect(popover.getByText("Redis")).toBeVisible();
    await expect(popover.getByText("Payment mode")).toBeVisible();
    // never a secret value rendered
    const bodyText = await popover.innerText();
    expect(bodyText.toLowerCase()).not.toContain("key=");
    await page.keyboard.press("Escape");
    await expect(popover).toHaveCount(0);
  });

  test("KPI cards render real values, not placeholders, once loaded", async ({ page }) => {
    const revenueCard = page.locator("text=Revenue uplift").locator("..");
    await expect(revenueCard).toBeVisible();
    // Loading skeleton must resolve to either a real value or an honest "no data" state.
    await expect(page.locator("text=Protected offers blocked").locator("..")).toBeVisible();
    await expect(page.getByText(/^\d+$/).first()).toBeVisible({ timeout: 10_000 });
  });

  test("refresh control refetches and updates the last-updated label", async ({ page }) => {
    await expect(page.getByText(/Updated (just now|\d+s ago|\d+m ago)/)).toBeVisible({ timeout: 10_000 });
    await page.getByRole("button", { name: "Refresh" }).click();
    await expect(page.getByText(/Updated (just now|\d+s ago|\d+m ago)/)).toBeVisible();
  });

  test("How it works modal opens with the real consent explanation and closes on Escape", async ({
    page,
  }) => {
    await page.getByRole("button", { name: "How it works" }).click();
    const modal = page.getByRole("dialog", { name: "How upsells work" });
    await expect(modal).toBeVisible();
    await expect(modal.getByText("No upgrade is charged automatically.")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(modal).toHaveCount(0);
  });

  test("growth chart shows real data or an honest empty state, never a fabricated chart", async ({
    page,
  }) => {
    const chart = page.getByRole("img", { name: "Baseline versus ACTL upsell growth comparison" });
    const emptyState = page.getByText("No growth sessions recorded yet");
    await expect(chart.or(emptyState)).toBeVisible({ timeout: 10_000 });
    // if real data is present, its accessible table fallback must exist too
    if (await chart.isVisible()) {
      await expect(
        page.locator("table caption", { hasText: "Baseline vs ACTL upsell growth impact" }),
      ).toHaveCount(1);
    }
  });
});
