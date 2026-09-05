import { expect, test } from "@playwright/test";

// §28 P12 follow-up: Trust Lab (orders.source='demo_lab') and growth
// simulation (orders.source='growth_simulation') rows must never be mixed
// into the Merchant dashboard's real operational view -- "Live operations"
// (organic, the default) and "Demo activity" are two disjoint scopes, never
// one badged, combined list.

test.describe("Merchant dashboard: organic vs demo scope", () => {
  test.skip(({ isMobile }) => isMobile, "desktop-only; see merchant-mobile.spec.ts");

  test.beforeEach(async ({ page }) => {
    await page.goto("/merchant");
    await page.getByRole("button", { name: "Live orders", exact: true }).click();
  });

  test("defaults to the Live operations (organic) scope", async ({ page }) => {
    await expect(page.getByRole("button", { name: "Live operations" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(page.getByRole("button", { name: "Demo activity" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  test("switching to Demo activity requests the demo scope and shows only tagged rows", async ({
    page,
  }) => {
    const demoRequest = page.waitForRequest(
      (req) => req.url().includes("/merchant/v1/orders") && req.url().includes("scope=demo"),
    );
    await page.getByRole("button", { name: "Demo activity" }).click();
    await demoRequest;
    await expect(page.getByRole("button", { name: "Demo activity" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    // If this shared environment already has demo/growth rows, every one
    // shown here must carry its source badge -- an organic row (no badge)
    // must never appear in this scope.
    const rows = page.locator("tbody tr");
    const count = await rows.count();
    for (let i = 0; i < count; i++) {
      await expect(rows.nth(i)).toContainText(/Demo|Simulated/);
    }
  });

  test("clean/no-organic-data renders the honest empty state, never a fabricated table", async ({
    page,
  }) => {
    await page.route("**/merchant/v1/orders*scope=organic*", async (route) => {
      await route.fulfill({ json: { items: [] } });
    });
    await page.reload();
    await page.getByRole("button", { name: "Live orders", exact: true }).click();

    await expect(page.getByText("No live bookings yet")).toBeVisible();
    await expect(
      page.getByText(
        "Complete a buyer journey to see live orders, policy decisions, and audit proofs here.",
      ),
    ).toBeVisible();
    await expect(page.locator("table")).toHaveCount(0);
  });

  test("clean demo-scope also renders an honest empty state, not the organic copy", async ({
    page,
  }) => {
    await page.route("**/merchant/v1/orders*scope=demo*", async (route) => {
      await route.fulfill({ json: { items: [] } });
    });
    await page.getByRole("button", { name: "Demo activity" }).click();

    await expect(page.getByText("No demo activity yet")).toBeVisible();
    await expect(page.getByText("No live bookings yet")).toHaveCount(0);
  });
});
