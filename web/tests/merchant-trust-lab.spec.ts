import { expect, test, type Page } from "@playwright/test";

// Replaces merchant-demo-lab.spec.ts -- that file targeted the old static
// four-card "Demo Lab" UI (a "Run demo" button per card, a flat "Completed"
// evidence list), which the ACTL Trust Lab redesign replaces outright.

test.describe("ACTL Trust Lab: live, real event-driven runs", () => {
  test.skip(({ isMobile }) => isMobile, "desktop-only; see mobile smoke below");

  test.beforeEach(async ({ page }) => {
    await page.goto("/merchant");
    await page.getByRole("button", { name: "Trust Lab", exact: true }).click();
  });

  test("running a scenario polls the backend for progressive real event updates", async ({
    page,
  }) => {
    const polls: string[] = [];
    page.on("response", (res) => {
      if (/\/merchant\/v1\/demo-runs\/run_/.test(res.url()) && res.request().method() === "GET") {
        polls.push(res.url());
      }
    });

    await page.getByRole("button", { name: "Run this scenario" }).first().click();
    const dialog = page.getByRole("dialog", { name: "Trust Lab run" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText("passed", { exact: true })).toBeVisible({ timeout: 15_000 });

    // The frontend never simulates progress -- every state shown came from
    // a real poll of the backend's own run record, and there must be more
    // than one (queued/running seen at least once before the terminal one).
    expect(polls.length).toBeGreaterThan(1);
  });

  test("a completed run shows Detected / Contained / Final state / Why protected", async ({
    page,
  }) => {
    await page.getByRole("button", { name: "Run this scenario" }).first().click();
    const dialog = page.getByRole("dialog", { name: "Trust Lab run" });
    await expect(dialog.getByText("passed", { exact: true })).toBeVisible({ timeout: 15_000 });

    await expect(dialog.getByText("Detected", { exact: true })).toBeVisible();
    await expect(dialog.getByText("Contained", { exact: true })).toBeVisible();
    await expect(dialog.getByText("Final state", { exact: true })).toBeVisible();
    await expect(dialog.getByText("Why the buyer is protected", { exact: true })).toBeVisible();
  });

  test("stale-price timeline shows the real G5 / STALE_PRICE denial, then recovery", async ({
    page,
  }) => {
    await page.getByRole("button", { name: "Run this scenario" }).nth(0).click();
    const dialog = page.getByRole("dialog", { name: "Trust Lab run" });
    await expect(dialog.getByText("passed", { exact: true })).toBeVisible({ timeout: 15_000 });

    await expect(dialog.getByText("Gate G5 blocked the purchase")).toBeVisible();
    await expect(dialog.getByText(/STALE_PRICE/).first()).toBeVisible();
    await expect(dialog.getByText("Order proposed and allowed")).toBeVisible();
    await expect(dialog.getByText("Out-of-band price change")).toBeVisible();

    // The rejected stale attempt and the safe retry must read as two
    // clearly separate attempts, not one undifferentiated timeline.
    await expect(dialog.getByText("Attempt 1 -- stale quote (rejected)")).toBeVisible();
    await expect(dialog.getByText("Attempt 2 -- safe retry (completed)")).toBeVisible();

    // Final summary must never be a bare "CAPTURED" -- it must carry the
    // full blocked -> re-quoted -> retried story.
    await expect(
      dialog.getByText("Stale quote blocked before payment. Fresh quote issued. Safe retry completed."),
    ).toBeVisible();
    await expect(dialog.getByText("CAPTURED", { exact: true })).toHaveCount(0);
  });

  test("declined-payment timeline shows compensation and a zero final reservation", async ({
    page,
  }) => {
    await page.getByRole("button", { name: "Run this scenario" }).nth(1).click();
    const dialog = page.getByRole("dialog", { name: "Trust Lab run" });
    await expect(dialog.getByText("passed", { exact: true })).toBeVisible({ timeout: 15_000 });

    await expect(dialog.getByText("Compensation applied")).toBeVisible();
    await expect(dialog.getByText(/reason: payment_declined/)).toBeVisible();
    // Ledger trust control reads "<held> → ₹0" -- the real final balance.
    await expect(dialog.getByText("LEDGER")).toBeVisible();
    await expect(dialog.getByText(/→ ₹0/)).toBeVisible();
  });

  test("LLM-down timeline shows the real deterministic fallback before any money step", async ({
    page,
  }) => {
    await page.getByRole("button", { name: "Run this scenario" }).nth(2).click();
    const dialog = page.getByRole("dialog", { name: "Trust Lab run" });
    await expect(dialog.getByText("passed", { exact: true })).toBeVisible({ timeout: 15_000 });

    await expect(dialog.getByText("LLM unavailable -- deterministic extraction used")).toBeVisible();
    await expect(dialog.getByText("Deterministic ranking used")).toBeVisible();
    await expect(
      dialog.getByText("No money decision was ever delegated to the LLM"),
    ).toBeVisible();
  });

  test("verify-chain timeline never falsely claims Monad anchoring", async ({ page }) => {
    await page.getByRole("button", { name: "Run this scenario" }).nth(3).click();
    const dialog = page.getByRole("dialog", { name: "Trust Lab run" });
    await expect(dialog.getByText("passed", { exact: true })).toBeVisible({ timeout: 20_000 });

    await expect(dialog.getByText(/entries independently recomputed/)).toBeVisible();
    // ANCHOR_PROVIDER=noop by default in this environment -- every real
    // checkpoint this run reports on must say so, never "Anchored".
    await expect(dialog.getByText("Anchored on Monad Testnet")).toHaveCount(0);
    // No order exists for this scenario -- "Open order proof" must not appear.
    await expect(dialog.getByRole("button", { name: "Open order proof" })).toHaveCount(0);

    // A long-lived chain can carry many real checkpoints -- the main
    // timeline must show one truthful aggregate (never dozens of rows),
    // with the actual per-checkpoint detail still reachable underneath.
    const summary = dialog.getByText(/^\d+ checkpoints matched$/);
    if (await summary.count()) {
      await expect(summary.first()).toBeVisible();
      await expect(dialog.getByText(/anchored, \d+ not anchored/)).toBeVisible();
      const details = dialog.getByText(/^Show all \d+ checkpoints$/);
      await expect(details).toBeVisible();
      await details.click();
      await expect(dialog.getByText(/Checkpoint seq \d+-\d+ Merkle root matches/).first()).toBeVisible();
    }
  });

  test("running the same scenario twice never breaks", async ({ page }) => {
    const runButton = page.getByRole("button", { name: "Run this scenario" }).first();
    const dialog = page.getByRole("dialog", { name: "Trust Lab run" });

    await runButton.click();
    await expect(dialog.getByText("passed", { exact: true })).toBeVisible({ timeout: 15_000 });
    await dialog.getByRole("button", { name: "Run again" }).click();
    await expect(dialog.getByText("passed", { exact: true })).toBeVisible({ timeout: 15_000 });
  });

  test("Play full trust tour runs all four scenarios one at a time and ends with a summary", async ({
    page,
  }) => {
    await page.getByRole("button", { name: "Play full trust tour" }).click();
    const dialog = page.getByRole("dialog", { name: "Trust Lab run" });

    for (let step = 1; step <= 3; step++) {
      await expect(dialog.getByText("passed", { exact: true })).toBeVisible({ timeout: 20_000 });
      await dialog.getByRole("button", { name: /^Next:/ }).click();
    }
    await expect(dialog.getByText("passed", { exact: true })).toBeVisible({ timeout: 20_000 });
    await dialog.getByRole("button", { name: "View tour summary" }).click();

    const summary = page.getByRole("dialog", { name: "Trust tour summary" });
    await expect(summary).toBeVisible();
    await expect(summary.getByText("Trust tour complete")).toBeVisible();
    await expect(summary.getByText("stale price")).toBeVisible();
    await expect(summary.getByText("declined")).toBeVisible();
    await expect(summary.getByText("llm down")).toBeVisible();
    await expect(summary.getByText("verify chain")).toBeVisible();
  });
});

async function assertTrustLabRenders(page: Page, colorScheme: "light" | "dark") {
  await page.emulateMedia({ colorScheme });
  await page.goto("/merchant");
  await page.getByRole("button", { name: "Trust Lab", exact: true }).click();
  await expect(page.getByText("ACTL Trust Lab")).toBeVisible();
  await expect(page.getByText("Safe local simulator").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Run this scenario" }).first()).toBeVisible();
}

test.describe("ACTL Trust Lab: theme", () => {
  test.skip(({ isMobile }) => isMobile, "desktop-only; see mobile smoke below");

  test("renders correctly in light mode", async ({ page }) => {
    await assertTrustLabRenders(page, "light");
  });

  test("renders correctly in dark mode, including an open run panel", async ({ page }) => {
    await assertTrustLabRenders(page, "dark");
    await page.getByRole("button", { name: "Run this scenario" }).first().click();
    const dialog = page.getByRole("dialog", { name: "Trust Lab run" });
    await expect(dialog.getByText("passed", { exact: true })).toBeVisible({ timeout: 15_000 });
    await expect(dialog.getByText("TRUST CONTROLS")).toBeVisible();
  });
});

test.describe("ACTL Trust Lab: mobile", () => {
  test.skip(({ isMobile }) => !isMobile, "mobile-viewport-only check");

  test("scenario cards and a run are readable at mobile width", async ({ page }) => {
    await page.goto("/merchant");
    await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("button", { name: "Trust Lab", exact: true }).click();
    await expect(page.getByText("ACTL Trust Lab")).toBeVisible();

    await page.getByRole("button", { name: "Run this scenario" }).first().click();
    const dialog = page.getByRole("dialog", { name: "Trust Lab run" });
    await expect(dialog.getByText("passed", { exact: true })).toBeVisible({ timeout: 15_000 });
    await expect(dialog.getByText("TRUST CONTROLS")).toBeVisible();
  });
});
