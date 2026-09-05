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

    // Regression proof for the real product bug: the mobile assistant
    // launcher used to be a wide bottom-right chip that could physically
    // cover a hotel card's Select button once actionability scrolling
    // brought it into view. It's now a compact bottom-LEFT FAB,
    // structurally on the opposite side of the screen from HotelCard's
    // own right-aligned action row -- checked here on the X axis only,
    // deliberately never after an explicit scroll call: `position: fixed`
    // (the launcher) and normal in-flow content (the button) both keep
    // the same X coordinate regardless of vertical scroll position, so
    // this is a real, deterministic proof that holds at every scroll
    // depth, not a single scroll-dependent snapshot. (An earlier version
    // of this assertion called the non-standard scrollIntoViewIfNeeded()
    // and checked elementFromPoint at its landing spot -- that API's
    // exact landing position turned out to differ between this sandbox's
    // Chromium build and GitHub Actions' runner, which is exactly the
    // kind of environment-dependent flakiness a real regression proof
    // must not rely on.)
    const selectButton = page.getByTestId("hotel-select-HTL-GOA-BUDGET-RM");
    const launcher = page.getByRole("button", { name: "Open ACTL travel assistant" });
    const [selectBox, launcherBox] = await Promise.all([
      selectButton.boundingBox(),
      launcher.boundingBox(),
    ]);
    expect(selectBox).not.toBeNull();
    expect(launcherBox).not.toBeNull();
    expect(selectBox!.x).toBeGreaterThanOrEqual(launcherBox!.x + launcherBox!.width);

    // The real click -- Playwright's own actionability engine handles
    // whatever scrolling is needed. If it ever landed on the launcher
    // instead of the button, this would open the chat panel, not select
    // a hotel, and the assertion below would fail.
    await selectButton.click();
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
