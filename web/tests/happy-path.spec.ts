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

    // "View proof" is the one working confirmation action left -- it
    // navigates to the real merchant proof route with the exact order id,
    // never a second, buyer-app-local proof implementation.
    await page.getByRole("button", { name: "View proof" }).click();
    await expect(page).toHaveURL(/\/merchant\?order_id=ord_[^&]+&panel=proof/, { timeout: 10_000 });
    const orderId = decodeURIComponent(page.url().match(/order_id=([^&]+)/)![1]);

    // The merchant Order Explorer / proof panel opens automatically for
    // that exact order, with a real causal timeline. Scoped to the dialog
    // throughout -- the same order id is also visible in the Live Orders
    // table row behind it, so an unscoped page-wide lookup is ambiguous.
    const explorer = page.getByRole("dialog", { name: "Order Explorer" });
    await expect(explorer).toBeVisible({ timeout: 10_000 });
    await expect(explorer.getByText(orderId, { exact: true })).toBeVisible();
    await expect(explorer.getByText("Mandate locked")).toBeVisible();
    await expect(explorer.getByText("Quote issued")).toBeVisible();
    await expect(explorer.getByText("Ledger settled")).toBeVisible();

    // ANCHOR_PROVIDER=noop by default -- this order's checkpoint is never
    // falsely claimed as Monad-anchored, whether it hasn't crossed a
    // checkpoint boundary yet or has but has no on-chain tx.
    await expect(
      explorer
        .getByText("Awaiting the next audit checkpoint")
        .or(explorer.getByText(/hasn't been anchored/i))
        .first(),
    ).toBeVisible();
    await expect(explorer.getByText("Anchored on Monad Testnet")).toHaveCount(0);

    await page.getByRole("button", { name: "Back to buyer" }).click();
    await expect(page).toHaveURL("/");

    const bodyText = await page.locator("body").innerText();
    for (const secret of SECRET_STRINGS) {
      expect(bodyText.toLowerCase()).not.toContain(secret);
    }
  });

  test("the chat panel's own View proof button reaches the same merchant route", async ({ page }) => {
    await page.goto("/");
    await lockMandate(page, { budgetRupees: 30000, nights: 2 });
    await page.getByTestId("hotel-select-HTL-GOA-BUDGET-RM").click();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Continue to secure checkout" }).click();
    await expect(page.getByText("Booking confirmed")).toBeVisible({ timeout: 15_000 });
    await page.getByRole("button", { name: "Close quote" }).click();

    // The buyer never opened the chat panel for this booking -- open it
    // now and use its own order-status card's "View proof" instead.
    await page.getByRole("button", { name: "Open ACTL travel assistant" }).click();
    const chatPanel = page.getByRole("complementary", { name: "ACTL travel assistant" });
    await expect(chatPanel.getByText(/Booking CAPTURED/)).toBeVisible({ timeout: 10_000 });

    await chatPanel.getByRole("button", { name: "View proof" }).click();
    await expect(page).toHaveURL(/\/merchant\?order_id=ord_[^&]+&panel=proof/, { timeout: 10_000 });
    await expect(page.getByRole("dialog", { name: "Order Explorer" })).toBeVisible({ timeout: 10_000 });
  });

  test("declining the contextual upsell offer charges nothing extra", async ({ page }) => {
    await page.goto("/");
    await lockMandate(page, { budgetRupees: 30000, nights: 2 });
    await page.getByTestId("hotel-select-HTL-GOA-BUDGET-RM").click();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Continue to secure checkout" }).click();
    await expect(page.getByText("Booking confirmed")).toBeVisible({ timeout: 15_000 });
    await page.getByRole("button", { name: "Close quote" }).click();

    await page.getByRole("button", { name: "Open ACTL travel assistant" }).click();
    const chatPanel = page.getByRole("complementary", { name: "ACTL travel assistant" });
    await expect(
      chatPanel.getByText("Your Goa stay is confirmed. Want to see optional extras for this trip?"),
    ).toBeVisible({ timeout: 15_000 });

    await chatPanel.getByRole("button", { name: "No thanks" }).click();
    await expect(
      chatPanel.getByText("Your Goa stay is confirmed. Want to see optional extras for this trip?"),
    ).toHaveCount(0);
  });

  test("accepting a contextual upsell requires explicit separate approval and settles for real", async ({
    page,
  }) => {
    await page.goto("/");
    await lockMandate(page, { budgetRupees: 30000, nights: 2 });
    await page.getByTestId("hotel-select-HTL-GOA-BUDGET-RM").click();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Continue to secure checkout" }).click();
    await expect(page.getByText("Booking confirmed")).toBeVisible({ timeout: 15_000 });
    await page.getByRole("button", { name: "Close quote" }).click();

    await page.getByRole("button", { name: "Open ACTL travel assistant" }).click();
    const chatPanel = page.getByRole("complementary", { name: "ACTL travel assistant" });
    await expect(
      chatPanel.getByText("Your Goa stay is confirmed. Want to see optional extras for this trip?"),
    ).toBeVisible({ timeout: 15_000 });

    await chatPanel.getByRole("button", { name: "Show options" }).click();
    await expect(chatPanel.getByText("Optional extras for this trip")).toBeVisible();
    await chatPanel.getByText("Daily breakfast").click();

    // Explicit, separate review/approval step -- selecting an offer alone
    // must never trigger a purchase.
    await expect(chatPanel.getByText(/Review: Daily breakfast/)).toBeVisible();
    await expect(chatPanel.getByText(/separate from your original booking/)).toBeVisible();
    await expect(chatPanel.getByRole("button", { name: "Approve" })).toBeVisible();

    await chatPanel.getByRole("button", { name: "Approve" }).click();
    await expect(chatPanel.getByText(/settled separately under its own mandate/)).toBeVisible({
      timeout: 15_000,
    });

    // The add-on's own proof is reachable through the same merchant route.
    // (the base booking's own order card also has a "View proof" button
    // still visible above this one -- the add-on's is the one that
    // appears later, inside the upsell result card.)
    await chatPanel.getByRole("button", { name: "View proof" }).last().click();
    await expect(page).toHaveURL(/\/merchant\?order_id=ord_[^&]+&panel=proof/, { timeout: 10_000 });
    await expect(page.getByRole("dialog", { name: "Order Explorer" })).toBeVisible({ timeout: 10_000 });
  });
});
