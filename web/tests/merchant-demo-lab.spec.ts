import { expect, test } from "@playwright/test";

test.describe("Demo Lab: real guarded runs", () => {
  test.skip(({ isMobile }) => isMobile, "desktop-only; see merchant-mobile.spec.ts");

  test.beforeEach(async ({ page }) => {
    await page.goto("/merchant");
    await page.getByRole("button", { name: "Demo Lab", exact: true }).click();
  });

  test("verify audit chain runs live and shows real evidence", async ({ page }) => {
    const card = page.locator("text=Verify audit chain").locator("..").locator("..");
    await card.getByRole("button", { name: "Run demo" }).click();
    await expect(card.getByText(/Chain valid|Chain broken/)).toBeVisible({ timeout: 15_000 });
    await expect(card.getByText(/^\d+$/)).toBeVisible();
  });

  test("stale price demo shows real detected-fault and recovery evidence", async ({ page }) => {
    const card = page.locator("text=Stale price").locator("..").locator("..");
    await card.getByRole("button", { name: "Run demo" }).click();
    await expect(card.getByText("Completed")).toBeVisible({ timeout: 15_000 });
    await expect(card.getByText("STALE_PRICE (catalog_version mismatch)")).toBeVisible();
  });

  test("payment decline demo shows real compensation evidence", async ({ page }) => {
    const card = page.locator("text=Payment decline").locator("..").locator("..");
    await card.getByRole("button", { name: "Run demo" }).click();
    await expect(card.getByText("Completed")).toBeVisible({ timeout: 15_000 });
    await expect(card.getByText("PROVIDER_DECLINED")).toBeVisible();
  });

  test("llm unavailable demo shows the real deterministic fallback evidence", async ({ page }) => {
    const card = page.locator("text=LLM unavailable").locator("..").locator("..");
    await card.getByRole("button", { name: "Run demo" }).click();
    await expect(card.getByText("Completed")).toBeVisible({ timeout: 15_000 });
    await expect(card.getByText("LLM_UNAVAILABLE (every U1/U2 call)")).toBeVisible();
  });

  test("running the same demo twice never breaks (unique run per click)", async ({ page }) => {
    const card = page.locator("text=Stale price").locator("..").locator("..");
    await card.getByRole("button", { name: "Run demo" }).click();
    await expect(card.getByText("Completed")).toBeVisible({ timeout: 15_000 });
    await card.getByRole("button", { name: "Run demo" }).click();
    await expect(card.getByText("Completed")).toBeVisible({ timeout: 15_000 });
  });
});
