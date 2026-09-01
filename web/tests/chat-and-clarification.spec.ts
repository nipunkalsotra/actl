import { expect, test } from "@playwright/test";
import { openChat } from "./helpers";

test.describe("assistant chat: open/close, chips, clarification never invents a budget", () => {
  // Desktop-specific selectors (e.g. header's text "My trips"/"Help"
  // buttons are hidden below sm); mobile's own chat-as-sheet behavior is
  // covered separately by mobile.spec.ts.
  test.skip(({ isMobile }) => isMobile, "desktop-only; see mobile.spec.ts");

  test.beforeEach(async ({ page }) => {
    await page.goto("/");
  });

  test("chat opens from the launcher and closes with the X button and Escape", async ({ page }) => {
    await expect(page.getByRole("button", { name: "Open ACTL travel assistant" })).toBeVisible();
    await openChat(page);
    await expect(page.getByText("ACTL Travel Assistant", { exact: true })).toBeVisible();
    await expect(page.getByText("Safe booking mode")).toBeVisible();

    await page.getByRole("button", { name: "Close assistant" }).click();
    await expect(page.getByText("ACTL Travel Assistant", { exact: true })).toHaveCount(0);

    await openChat(page);
    await page.keyboard.press("Escape");
    await expect(page.getByText("ACTL Travel Assistant", { exact: true })).toHaveCount(0);
  });

  test("a message with no budget gets a clarification, never an invented mandate", async ({ page }) => {
    await openChat(page);
    await page.getByLabel("Message ACTL assistant").fill("Book me something nice in Goa.");
    await page.getByRole("button", { name: "Send message" }).click();

    // A clarification question appears, and the structured fallback form
    // affordance is offered -- never a "Locked" mandate straight from an
    // unbudgeted message.
    await expect(page.getByText("What's your total budget for this booking?")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByRole("button", { name: "Fill mandate details" })).toBeVisible();
    await expect(page.getByText("Locked", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Draft", { exact: true })).toBeVisible();
  });

  test("quick chips post a real message and update the matching filter", async ({ page }) => {
    // Nights defaults to 2 -- bump it away first so the chip's effect is observable.
    await page.getByRole("button", { name: "Increase nights" }).click();
    await page.getByRole("button", { name: "Increase nights" }).click();
    await expect(page.getByText("4 nights", { exact: true })).toBeVisible();

    await openChat(page);
    await page.getByRole("button", { name: "2 nights" }).click();
    await expect(page.getByText("I need it for 2 nights.")).toBeVisible();

    await page.getByRole("button", { name: "Close assistant" }).click();
    // FiltersCard's nights stepper reflects the same shared state.
    await expect(page.getByText("2 nights", { exact: true })).toBeVisible();
  });
});
