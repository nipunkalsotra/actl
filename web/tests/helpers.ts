import type { Page } from "@playwright/test";
import { expect } from "@playwright/test";

export async function openChat(page: Page) {
  await page.getByRole("button", { name: "Open ACTL travel assistant" }).click();
}

/** Locks a real mandate through the buyer-facing chat flow -- always via
 * the structured fallback form (deterministic regardless of whether an
 * LLM is configured in this environment), same UI path a buyer using the
 * "Fill mandate details" affordance would take. */
export async function lockMandate(
  page: Page,
  opts?: { budgetRupees?: number; nights?: number },
) {
  await openChat(page);
  await page.getByLabel("Message ACTL assistant").fill("Book me something nice in Goa.");
  await page.getByRole("button", { name: "Send message" }).click();

  const fillDetailsBtn = page.getByRole("button", { name: "Fill mandate details" });
  const lockBtn = page.getByRole("button", { name: "Lock & sign mandate" });
  await expect(fillDetailsBtn.or(lockBtn)).toBeVisible({ timeout: 15_000 });

  if (await fillDetailsBtn.isVisible()) {
    await fillDetailsBtn.click();
    if (opts?.budgetRupees) {
      await page.getByLabel("Total budget", { exact: true }).fill(String(opts.budgetRupees));
    }
    if (opts?.nights) await page.getByLabel("Nights", { exact: true }).fill(String(opts.nights));
    await page.getByRole("button", { name: "Use these details" }).click();
  }

  await expect(lockBtn).toBeVisible({ timeout: 10_000 });
  await lockBtn.click();
  await expect(page.getByText("Locked", { exact: true })).toBeVisible({ timeout: 10_000 });

  // Close the panel -- a locked mandate's own message already tells the
  // buyer to go browse the catalog now; leaving it open would otherwise
  // sit on top of catalog/sticky-bar controls a short results list can
  // leave underneath it.
  await page.getByRole("button", { name: "Close assistant" }).click();
}
