import { expect, test, type Page } from "@playwright/test";
import { lockMandate } from "./helpers";

async function getDataTheme(page: Page): Promise<string | null> {
  return page.evaluate(() => document.documentElement.getAttribute("data-theme"));
}

async function getStoredTheme(page: Page): Promise<string | null> {
  return page.evaluate(() => localStorage.getItem("actl-theme"));
}

test.describe("theme system", () => {
  // Desktop-only: these are interaction/persistence checks, not layout
  // checks -- mobile coverage is the dedicated smoke test below.
  test.skip(({ isMobile }) => isMobile, "desktop-only; see mobile smoke below");

  test("defaults to system preference when no explicit choice is stored", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto("/");
    expect(await getDataTheme(page)).toBe("dark");
    expect(await getStoredTheme(page)).toBeNull();

    await page.emulateMedia({ colorScheme: "light" });
    await page.reload();
    expect(await getDataTheme(page)).toBe("light");
    expect(await getStoredTheme(page)).toBeNull();
  });

  test("falls back to light when the system reports no preference", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "no-preference" });
    await page.goto("/");
    expect(await getDataTheme(page)).toBe("light");
  });

  test("explicit choice persists across reload and overrides system preference", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/");
    await page.getByRole("button", { name: "Switch to dark theme" }).click();
    expect(await getDataTheme(page)).toBe("dark");
    expect(await getStoredTheme(page)).toBe("dark");

    // OS still reports light -- the stored explicit choice must win.
    await page.reload();
    expect(await getDataTheme(page)).toBe("dark");
    await expect(page.getByRole("button", { name: "Switch to light theme" })).toBeVisible();
  });

  test("toggle works from the Buyer header", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/");
    await page.getByRole("button", { name: "Switch to dark theme" }).click();
    expect(await getDataTheme(page)).toBe("dark");
  });

  test("toggle works from the Merchant header", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/merchant");
    await page.getByRole("button", { name: "Switch to dark theme" }).click();
    expect(await getDataTheme(page)).toBe("dark");
  });

  test("toggle is keyboard accessible with a correct accessible name", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/");
    const toggle = page.getByRole("button", { name: "Switch to dark theme" });
    await toggle.focus();
    await expect(toggle).toBeFocused();
    await page.keyboard.press("Enter");
    expect(await getDataTheme(page)).toBe("dark");
    // Same control, now announcing the opposite action -- and still
    // holding keyboard focus after the click.
    await expect(page.getByRole("button", { name: "Switch to light theme" })).toBeFocused();
  });

  test("switching theme does not reset buyer journey state", async ({ page }) => {
    await page.goto("/");
    await lockMandate(page, { budgetRupees: 30000, nights: 2 });
    await page.getByTestId("hotel-select-HTL-GOA-BUDGET-RM").click();
    await expect(page.getByText("Selected: Budget Room")).toBeVisible();

    await page.getByRole("button", { name: "Switch to dark theme" }).click();
    expect(await getDataTheme(page)).toBe("dark");

    await expect(page.getByText("Selected: Budget Room")).toBeVisible();
    await expect(page.getByRole("button", { name: "Continue" })).toBeEnabled();
  });

  test("switching theme does not close an open Order Explorer drawer", async ({ page }) => {
    // The drawer's own backdrop legitimately covers the header while
    // open (same as every other header control -- that's the point of a
    // modal), so a header-button click isn't a reachable interaction
    // here. A live system-preference flip is: this app tracks it via
    // useMediaQuery whenever no explicit choice is stored, e.g. an OS
    // auto dark-mode switch firing while a drawer is open. Exercises the
    // same underlying requirement (theme state change must not unmount
    // sibling UI) without depending on a physically-blocked click.
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/merchant");
    await page.getByRole("button", { name: "Live orders", exact: true }).click();
    await page.locator("tbody tr").first().getByRole("button", { name: "View", exact: true }).click();
    const explorer = page.getByRole("dialog", { name: "Order Explorer" });
    await expect(explorer).toBeVisible();

    await page.emulateMedia({ colorScheme: "dark" });
    await expect.poll(() => getDataTheme(page)).toBe("dark");
    await expect(explorer).toBeVisible();
    await expect(explorer.getByText("Mandate locked")).toBeVisible();
  });

  test("the merchant proof deep-link still opens correctly in dark mode", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Switch to dark theme" }).click();
    await lockMandate(page, { budgetRupees: 30000, nights: 2 });
    await page.getByTestId("hotel-select-HTL-GOA-BUDGET-RM").click();
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByText("Within your mandate")).toBeVisible({ timeout: 10_000 });
    await page.getByRole("button", { name: "Continue to secure checkout" }).click();
    await expect(page.getByText("Booking confirmed")).toBeVisible({ timeout: 15_000 });

    await page.getByRole("button", { name: "View proof" }).click();
    await expect(page).toHaveURL(/\/merchant\?order_id=ord_[^&]+&panel=proof/, { timeout: 10_000 });
    await expect(page.getByRole("dialog", { name: "Order Explorer" })).toBeVisible({ timeout: 10_000 });
    // Deep-link navigation didn't reset the theme choice either.
    expect(await getDataTheme(page)).toBe("dark");
  });

  test("dark surfaces use theme tokens, not hard-coded light-only colours", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/");
    const lightPage = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    const lightHeader = await page.evaluate(
      () => getComputedStyle(document.querySelector("header")!).backgroundColor,
    );

    await page.getByRole("button", { name: "Switch to dark theme" }).click();
    // The 200ms color transition (plus a cold Vite dev-server first
    // paint) means the very next frame can still report the pre-toggle
    // computed value -- poll both past it instead of a fixed sleep.
    await expect
      .poll(() => page.evaluate(() => getComputedStyle(document.body).backgroundColor))
      .not.toBe(lightPage);
    await expect
      .poll(() => page.evaluate(() => getComputedStyle(document.querySelector("header")!).backgroundColor))
      .not.toBe(lightHeader);
    const darkPage = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    const darkHeader = await page.evaluate(
      () => getComputedStyle(document.querySelector("header")!).backgroundColor,
    );

    // Both the page canvas AND the elevated header/card surface actually
    // repaint -- proves these read from the theme-reactive custom
    // properties rather than a fixed light-only Tailwind color.
    expect(darkPage).not.toBe(lightPage);
    expect(darkHeader).not.toBe(lightHeader);
    // Header (elevated card) must stay visually distinct from the page
    // canvas in dark mode too, not collapse into the same flat color.
    expect(darkHeader).not.toBe(darkPage);
  });
});

test.describe("theme visual capture (desktop)", () => {
  test.skip(({ isMobile }) => isMobile, "desktop-only; see mobile smoke below");

  test("buyer and merchant render correctly in both themes", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light" });

    await page.goto("/");
    await expect(page.getByRole("button", { name: "Switch to dark theme" })).toBeVisible();
    await page.screenshot({ path: "test-results/theme-screenshots/buyer-light.png" });

    // Buyer dark mode with chat + mandate card open. lockMandate opens
    // the assistant panel itself and closes it again once done -- reopen
    // it once afterwards for the screenshot.
    await page.getByRole("button", { name: "Switch to dark theme" }).click();
    await lockMandate(page, { budgetRupees: 30000, nights: 2 });
    await page.getByRole("button", { name: "Open ACTL travel assistant" }).click();
    await expect(page.getByText("Locked", { exact: true }).first()).toBeVisible();
    await page.screenshot({ path: "test-results/theme-screenshots/buyer-dark-chat-mandate.png" });

    await page.getByRole("button", { name: "Switch to light theme" }).click();
    await page.goto("/merchant");
    await expect(page.getByRole("button", { name: "Switch to dark theme" })).toBeVisible();
    await page.screenshot({ path: "test-results/theme-screenshots/merchant-light.png" });

    // Merchant dark mode with the Order Explorer open.
    await page.getByRole("button", { name: "Switch to dark theme" }).click();
    await page.getByRole("button", { name: "Live orders", exact: true }).click();
    await page.locator("tbody tr").first().getByRole("button", { name: "View", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Order Explorer" })).toBeVisible();
    await page.screenshot({ path: "test-results/theme-screenshots/merchant-dark-order-explorer.png" });

    // Persistence after a real reload -- data-theme is already correct
    // pre-paint (checked immediately), then wait for real content so the
    // screenshot itself shows more than a blank canvas.
    await page.reload();
    expect(await getDataTheme(page)).toBe("dark");
    await expect(page.getByRole("button", { name: "Switch to light theme" })).toBeVisible();
    await page.screenshot({ path: "test-results/theme-screenshots/merchant-dark-after-reload.png" });
  });
});

test.describe("theme mobile smoke", () => {
  test.skip(({ isMobile }) => !isMobile, "mobile-viewport-only check");

  test("buyer and merchant render correctly at mobile width in dark mode", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Switch to light theme" })).toBeVisible();
    await expect(page.getByTestId("hotel-select-HTL-GOA-BUDGET-RM")).toBeVisible();
    await page.screenshot({ path: "test-results/theme-screenshots/buyer-mobile-dark.png" });

    await page.goto("/merchant");
    await expect(page.getByRole("button", { name: "Switch to light theme" })).toBeVisible();
    await page.screenshot({ path: "test-results/theme-screenshots/merchant-mobile-dark.png" });
  });
});
