import { expect, test, type Page } from "@playwright/test";

function boxesOverlap(
  a: { x: number; y: number; width: number; height: number },
  b: { x: number; y: number; width: number; height: number },
): boolean {
  return a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
}

/** What a real tap actually resolves to at the target's own center point --
 * the same thing Playwright's own actionability check verifies internally,
 * asserted directly here so this is a real regression proof, not just an
 * absence-of-visual-overlap heuristic. */
async function elementAtCenterOf(page: Page, box: { x: number; y: number; width: number; height: number }) {
  return page.evaluate(
    ([x, y]) => {
      const el = document.elementFromPoint(x, y);
      return el
        ? { tag: el.tagName, testId: el.getAttribute("data-testid"), ariaLabel: el.getAttribute("aria-label") }
        : null;
    },
    [box.x + box.width / 2, box.y + box.height / 2] as const,
  );
}

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
    // own right-aligned action row.
    //
    // scrollIntoViewIfNeeded() is not a workaround -- it's the exact same
    // browser API Playwright's own .click() uses internally to bring an
    // off-screen target into view, called here explicitly only so "the
    // actual click point" it settles on can be inspected before clicking,
    // with real geometry and a real elementFromPoint check -- not just
    // "the click eventually worked."
    const selectButton = page.getByTestId("hotel-select-HTL-GOA-BUDGET-RM");
    const launcher = page.getByRole("button", { name: "Open ACTL travel assistant" });

    await selectButton.scrollIntoViewIfNeeded();
    const [selectBox, launcherBox] = await Promise.all([
      selectButton.boundingBox(),
      launcher.boundingBox(),
    ]);
    expect(selectBox).not.toBeNull();
    expect(launcherBox).not.toBeNull();
    expect(boxesOverlap(selectBox!, launcherBox!)).toBe(false);

    const elAtCenter = await elementAtCenterOf(page, selectBox!);
    expect(elAtCenter?.testId).toBe("hotel-select-HTL-GOA-BUDGET-RM");

    // The real click -- the product fix is what makes this reliable now,
    // not a test-side scroll-to-center trick.
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
