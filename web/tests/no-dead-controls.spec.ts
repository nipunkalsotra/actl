import { expect, test } from "@playwright/test";
import { lockMandate } from "./helpers";

test.describe("every visible control does something real", () => {
  // Desktop-only: "My trips"/"Help" text buttons live in the profile menu
  // on mobile instead of the header; covered by mobile.spec.ts + the
  // profile-menu assertions here are exercised on desktop.
  test.skip(({ isMobile }) => isMobile, "desktop-only; see mobile.spec.ts");

  test.beforeEach(async ({ page }) => {
    await page.goto("/");
  });

  test("destination selector, My trips, Help, and profile menu all open real UI", async ({ page }) => {
    await expect(page.getByLabel("Destination")).toHaveValue("Goa");

    await page.getByRole("button", { name: "My trips" }).click();
    await expect(page.getByRole("dialog", { name: "My trips" })).toBeVisible();
    await page.getByRole("button", { name: "Close my trips" }).click();
    await expect(page.getByRole("dialog", { name: "My trips" })).toHaveCount(0);

    await page.getByRole("button", { name: "Help" }).click();
    await expect(page.getByRole("dialog", { name: /protects your booking/ })).toBeVisible();
    await page.getByRole("button", { name: "Close help" }).click();

    await page.getByRole("button", { name: "Open profile menu" }).click();
    await expect(page.getByText("Demo buyer")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByText("Demo buyer")).toHaveCount(0);
  });

  test("hotel details drawer opens from a card and can select the stay", async ({ page }) => {
    await page.getByTestId("hotel-card-HTL-GOA-BUDGET-RM").getByRole("button", { name: "View details" }).click();
    await expect(page.getByRole("dialog", { name: /details/ })).toBeVisible();
    await page.getByRole("button", { name: "Select this stay" }).click();
    await expect(page.getByText("Selected: Budget Room")).toBeVisible();
  });

  test("the ACTL logo confirms before discarding an active mandate", async ({ page }) => {
    await lockMandate(page);
    let dialogSeen = false;
    page.once("dialog", async (dialog) => {
      dialogSeen = true;
      await dialog.accept();
    });
    await page.getByRole("button", { name: /ACTL\./ }).click();
    await expect.poll(() => dialogSeen).toBe(true);
    await expect(page.getByText("Draft", { exact: true })).toBeVisible();
  });
});
