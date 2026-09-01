import { expect, test } from "@playwright/test";
import { lockMandate } from "./helpers";

const SECRET_STRINGS = ["mandate_signing_key", "quote_signing_key", "razorpay_key_secret", "admin_token"];

test.describe("happy path checkout", () => {
  // Desktop-only: the chat panel is a corner widget here, not a full sheet,
  // so this exercises the desktop layout specifically; mobile's own
  // checkout-adjacent full-height sheet is covered by mobile.spec.ts.
  test.skip(({ isMobile }) => isMobile, "desktop-only; see mobile.spec.ts");

  test("valid mandate -> real quote -> simulator checkout -> receipt -> audit proof", async ({ page }) => {
    await page.goto("/");
    await lockMandate(page, { budgetRupees: 30000, nights: 2 });

    // Best match unlocks once a mandate is locked, and lands on a real,
    // genuinely-within-budget item (never flagged "Over your trip budget").
    await expect(page.getByRole("button", { name: "Best match" })).toBeEnabled();
    const bestMatchCard = page.getByTestId("hotel-card-HTL-GOA-BUDGET-RM");
    await expect(bestMatchCard.getByText("Best match")).toBeVisible();
    await expect(bestMatchCard.getByText("Over your trip budget")).toHaveCount(0);

    await page.getByTestId("hotel-select-HTL-GOA-BUDGET-RM").click();
    await expect(page.getByText("Selected: Budget Room")).toBeVisible();

    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByText("Within your mandate")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/Quote expires in/)).toBeVisible();

    await page.getByRole("button", { name: "Continue to secure checkout" }).click();
    await expect(page.getByText("Booking confirmed")).toBeVisible({ timeout: 15_000 });

    await page.getByRole("button", { name: "View receipt" }).click();
    const chatPanel = page.getByRole("complementary", { name: "ACTL travel assistant" });
    await expect(chatPanel.getByText(/Booking CAPTURED/)).toBeVisible({ timeout: 10_000 });

    await chatPanel.getByRole("button", { name: "Verify proof" }).click();
    await expect(page.getByText(/Terminal outcome: CAPTURED/)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("Mandate", { exact: true })).toBeVisible();
    await expect(page.getByText("Safety checks", { exact: true })).toBeVisible();
    await expect(page.getByText("Ledger", { exact: true })).toBeVisible();

    const bodyText = await page.locator("body").innerText();
    for (const secret of SECRET_STRINGS) {
      expect(bodyText.toLowerCase()).not.toContain(secret);
    }
  });

  test("declining the upsell offer charges nothing extra", async ({ page }) => {
    await page.goto("/");
    await lockMandate(page, { budgetRupees: 30000, nights: 2 });
    await page.getByTestId("hotel-select-HTL-GOA-BUDGET-RM").click();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Continue to secure checkout" }).click();
    await expect(page.getByText("Booking confirmed")).toBeVisible({ timeout: 15_000 });
    await page.getByRole("button", { name: "View receipt" }).click();

    const declineBtn = page.getByRole("button", { name: "Decline" });
    await expect(declineBtn).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/never charged automatically/)).toBeVisible();
    await declineBtn.click();
    await expect(page.getByText("No problem — nothing extra was charged.")).toBeVisible();
  });
});
