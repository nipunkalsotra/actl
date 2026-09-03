import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { lockMandate } from "./helpers";

// Scoped to the four categories the theme rework actually touches --
// color, ARIA semantics, focusable/interactive controls, and landmarks.
// Other axe categories (e.g. best-practice document structure) are
// unrelated to a color-scheme change and out of scope for this check.
const COLOR_CONTRAST_RULES = ["color-contrast"];
const ARIA_RULE_PREFIX = "aria-";
const FOCUSABLE_CONTROL_RULES = [
  "button-name",
  "link-name",
  "input-button-name",
  "select-name",
  "nested-interactive",
  "scrollable-region-focusable",
  "tabindex",
  "focus-order-semantics",
  "frame-focusable-content",
];
const LANDMARK_RULE_PREFIX = "landmark-";
const LANDMARK_RULES = ["region"];

function isInScope(ruleId: string): boolean {
  return (
    COLOR_CONTRAST_RULES.includes(ruleId) ||
    ruleId.startsWith(ARIA_RULE_PREFIX) ||
    FOCUSABLE_CONTROL_RULES.includes(ruleId) ||
    ruleId.startsWith(LANDMARK_RULE_PREFIX) ||
    LANDMARK_RULES.includes(ruleId)
  );
}

async function assertNoSeriousViolations(page: Page, testInfo: TestInfo) {
  // axe samples actual rendered pixels for color-contrast -- scanning
  // mid framer-motion fade-in or mid the 200ms theme color-transition
  // captures a transient blended color, not the steady-state design.
  // Settle past both before asking axe to look.
  await page.waitForTimeout(500);
  const results = await new AxeBuilder({ page }).analyze();

  const blocking = results.violations.filter(
    (v) => isInScope(v.id) && (v.impact === "serious" || v.impact === "critical"),
  );

  if (blocking.length > 0) {
    const report = blocking
      .map(
        (v) =>
          `[${v.impact}] ${v.id}: ${v.help}\n${v.nodes
            .map((n) => `  - ${n.target.join(" ")}: ${n.failureSummary}`)
            .join("\n")}`,
      )
      .join("\n\n");
    await testInfo.attach("axe-violations", { body: report, contentType: "text/plain" });
    expect(blocking, report).toEqual([]);
  }
}

test.describe("theme accessibility (axe)", () => {
  test.skip(({ isMobile }) => isMobile, "desktop-only; theme tokens don't change by viewport");

  test("buyer core page -- light", async ({ page }, testInfo) => {
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Switch to dark theme" })).toBeVisible();
    await assertNoSeriousViolations(page, testInfo);
  });

  test("buyer core page -- dark", async ({ page }, testInfo) => {
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Switch to light theme" })).toBeVisible();
    await assertNoSeriousViolations(page, testInfo);
  });

  test("buyer chat + mandate panel open -- light", async ({ page }, testInfo) => {
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/");
    await lockMandate(page, { budgetRupees: 30000, nights: 2 });
    await page.getByRole("button", { name: "Open ACTL travel assistant" }).click();
    await expect(page.getByRole("complementary", { name: "ACTL travel assistant" })).toBeVisible();
    await assertNoSeriousViolations(page, testInfo);
  });

  test("buyer chat + mandate panel open -- dark", async ({ page }, testInfo) => {
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto("/");
    await lockMandate(page, { budgetRupees: 30000, nights: 2 });
    await page.getByRole("button", { name: "Open ACTL travel assistant" }).click();
    await expect(page.getByRole("complementary", { name: "ACTL travel assistant" })).toBeVisible();
    await assertNoSeriousViolations(page, testInfo);
  });

  test("merchant core page -- light", async ({ page }, testInfo) => {
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/merchant");
    await expect(page.getByRole("button", { name: "Switch to dark theme" })).toBeVisible();
    await assertNoSeriousViolations(page, testInfo);
  });

  test("merchant core page -- dark", async ({ page }, testInfo) => {
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto("/merchant");
    await expect(page.getByRole("button", { name: "Switch to light theme" })).toBeVisible();
    await assertNoSeriousViolations(page, testInfo);
  });

  test("merchant Order Explorer open -- light", async ({ page }, testInfo) => {
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/merchant");
    await page.getByRole("button", { name: "Live orders", exact: true }).click();
    await page.locator("tbody tr").first().getByRole("button", { name: "View", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Order Explorer" })).toBeVisible();
    await assertNoSeriousViolations(page, testInfo);
  });

  test("merchant Order Explorer open -- dark", async ({ page }, testInfo) => {
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto("/merchant");
    await page.getByRole("button", { name: "Live orders", exact: true }).click();
    await page.locator("tbody tr").first().getByRole("button", { name: "View", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Order Explorer" })).toBeVisible();
    await assertNoSeriousViolations(page, testInfo);
  });
});
