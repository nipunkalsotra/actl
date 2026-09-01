import { expect, test } from "@playwright/test";
import { lockMandate } from "./helpers";

test.describe("safe typed failures", () => {
  // Desktop-only: relies on the always-visible sidebar filters; mobile's
  // equivalent filter interaction is covered by mobile.spec.ts.
  test.skip(({ isMobile }) => isMobile, "desktop-only; see mobile.spec.ts");

  test("over-cap purchase is denied with a typed reason, never silently bypassed", async ({ page }) => {
    await page.goto("/");
    // Lock a 1-night, ₹1,000-total mandate and select HTL-GOA-BUDGET-RM
    // (95000/night) while it's still genuinely within both caps
    // (95000 x 1 <= 100000). The client-side "Best match"/selectability
    // guard (tested separately below) only ever evaluates feasibility at
    // browse time -- it does not retroactively re-validate an
    // already-selected trip when nights change afterward. Raising Nights
    // in the sidebar post-selection is exactly how a real total-cap
    // violation reaches checkout without the client catching it first, so
    // this is what proves the *server's* gate is still the real
    // authority, unchanged.
    await lockMandate(page, { budgetRupees: 1000, nights: 1 });
    await page.getByTestId("hotel-select-HTL-GOA-BUDGET-RM").click();
    await expect(page.getByText("Selected: Budget Room")).toBeVisible();

    await page.getByRole("button", { name: "Increase nights" }).click();
    await page.getByRole("button", { name: "Increase nights" }).click();
    await expect(page.getByText("3 nights", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "Continue" }).click();

    // The quote itself is issued (P4 scope never checks mandate bounds) --
    // the client-side hint shows it's outside the mandate before checkout.
    await expect(page.getByText("Outside your mandate")).toBeVisible({ timeout: 10_000 });

    await page.getByRole("button", { name: "Continue to secure checkout" }).click();
    await expect(page.getByText("Purchase declined", { exact: true })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(/total budget cap/)).toBeVisible();
    // Never a fake success after a real denial.
    await expect(page.getByText("Booking confirmed")).toHaveCount(0);
  });

  test("an item within its per-night cap but over the trip total is never Best match or a normal selectable recommendation", async ({
    page,
  }) => {
    // Same mandate shape as above (1 night, ₹1,000 total -> max_unit_minor
    // = max_total_minor = 100000): HTL-GOA-BUDGET-RM's real 95000/night
    // price is within the *unit* cap, but 3 nights (285000) blows the
    // *total* cap -- exactly the gap the server's own feasibility filter
    // (domain.agent.buyer.is_feasible) doesn't check, since it only ever
    // compares the per-night price. This is the client-side prefilter's
    // one job.
    await page.goto("/");
    await lockMandate(page, { budgetRupees: 1000, nights: 1 });

    await page.getByRole("button", { name: "Increase nights" }).click();
    await page.getByRole("button", { name: "Increase nights" }).click();
    await expect(page.getByText("3 nights", { exact: true })).toBeVisible();
    // Raise the browsing budget filter (independent of the locked mandate's
    // own fixed cap) so the item stays visible to browse/inspect -- this
    // test is about the feasibility *label*, not the pre-existing budget
    // filter that already hides unaffordable items from the list.
    const budgetSlider = page.getByLabel("Maximum total budget");
    await budgetSlider.focus();
    await budgetSlider.press("End");

    const card = page.getByTestId("hotel-card-HTL-GOA-BUDGET-RM");
    await expect(card).toBeVisible();
    await expect(card.getByText("Over your trip budget")).toBeVisible();
    await expect(card.getByText("Best match")).toHaveCount(0);

    const selectBtn = page.getByTestId("hotel-select-HTL-GOA-BUDGET-RM");
    await expect(selectBtn).toBeDisabled();
    await expect(selectBtn).toHaveText("Over budget");

    // HTL-GOA-BUDGET-RM is the cheapest real seeded item and the only one
    // within this mandate's per-night cap at all, so with it now over the
    // trip total there is no feasible alternative to promote -- "Best
    // match" must never mislabel the infeasible one instead of just
    // showing nothing. (The positive case -- a real feasible item getting
    // the badge -- is covered by happy-path.spec.ts's own best-match
    // selection under a normal mandate.)
    await page.getByRole("button", { name: "Best match" }).click();
    const bestMatchBadge = page.locator('[data-testid^="hotel-card-"]').filter({ hasText: "Best match" });
    await expect(bestMatchBadge).toHaveCount(0);
  });

  test("an expired quote is shown as a safe typed failure with a real recovery action", async ({
    page,
  }) => {
    await page.goto("/");
    await lockMandate(page, { budgetRupees: 30000, nights: 2 });

    await page.route("**/agent/v1/quote", async (route) => {
      const response = await route.fetch();
      const json = await response.json();
      json.expires_at = new Date(Date.now() - 60_000).toISOString();
      await route.fulfill({ response, json });
    });

    await page.getByTestId("hotel-select-HTL-GOA-BUDGET-RM").click();
    await page.getByRole("button", { name: "Continue" }).click();

    await expect(page.getByText("This quote has expired.")).toBeVisible({ timeout: 10_000 });
    const refreshBtn = page.getByRole("button", { name: "Get a fresh quote" });
    await expect(refreshBtn).toBeVisible();

    await page.unroute("**/agent/v1/quote");
    await refreshBtn.click();
    await expect(page.getByText(/Quote expires in/)).toBeVisible({ timeout: 10_000 });
  });
});
