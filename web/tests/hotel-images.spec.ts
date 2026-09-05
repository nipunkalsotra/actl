import { expect, test } from "@playwright/test";

// Curated, persistent `travel.hotel` seed SKUs (scripts/seed.py) only --
// every one of these must resolve to a real local WebP, never a broken
// image icon or a falsely-claimed photo of a real named hotel.
const CURATED_HOTELS: Record<string, string> = {
  "HTL-GOA-SEA-DLX": "htl-goa-sea-dlx.webp",
  "HTL-GOA-SUNSET-STD": "htl-goa-sunset-std.webp",
  "HTL-GOA-PALM-STE": "htl-goa-palm-ste.webp",
  "HTL-GOA-BUDGET-RM": "htl-goa-budget-rm.webp",
  "HTL-GOA-CLIFF-VIL": "htl-goa-cliff-vil.webp",
  "HTL-GOA-SOLDOUT-RM": "htl-goa-soldout-rm.webp",
};

test.describe("Buyer hotel card imagery", () => {
  test.skip(({ isMobile }) => isMobile, "desktop-only; layout covered elsewhere");

  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Stays in Goa" })).toBeVisible();
    // Show all six curated stays regardless of the refundable-only default.
    await page.getByLabel("Refundable only").uncheck();
  });

  test("Buyer catalog shows exactly the six curated hotels -- never Demo/Growth pollution", async ({
    page,
  }) => {
    // The exact reported bug: Trust Lab / growth-simulation rows used to
    // leak into this grid, making the honest count wrong and showing
    // internal-looking card names to a buyer.
    await expect(page.getByText("6 stays match your preferences")).toBeVisible();

    const cardNames = await page.locator('[data-testid^="hotel-card-"] h3').allTextContents();
    expect(cardNames).toHaveLength(6);
    for (const name of cardNames) {
      expect(name).not.toMatch(/Demo|Growth/i);
    }

    for (const sku of Object.keys(CURATED_HOTELS)) {
      await expect(page.getByTestId(`hotel-card-${sku}`).locator("img")).toHaveCount(1);
    }
  });

  test("every curated hotel SKU renders its own local image with truthful alt text", async ({
    page,
  }) => {
    for (const [sku, filename] of Object.entries(CURATED_HOTELS)) {
      const card = page.getByTestId(`hotel-card-${sku}`);
      await expect(card).toBeVisible();
      const img = card.locator("img");
      await expect(img).toHaveCount(1);
      await expect(img).toHaveAttribute("src", new RegExp(`/images/hotels/${filename}$`));
      const alt = await img.getAttribute("alt");
      expect(alt).toMatch(/^Representative view of /);
      // Never a broken image -- the element must actually have decoded.
      expect(await img.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBeGreaterThan(0);
    }
  });

  test("only the first, above-the-fold card image loads eagerly", async ({ page }) => {
    const images = page.locator('[data-testid^="hotel-card-"] img');
    await expect(images.first()).toHaveAttribute("loading", "eager");
    const count = await images.count();
    for (let i = 1; i < count; i++) {
      await expect(images.nth(i)).toHaveAttribute("loading", "lazy");
    }
  });

  test("the sold-out card keeps its real image visible alongside the Sold out badge", async ({
    page,
  }) => {
    const card = page.getByTestId("hotel-card-HTL-GOA-SOLDOUT-RM");
    await expect(card).toBeVisible();
    await expect(card.locator("img")).toHaveAttribute(
      "src",
      /\/images\/hotels\/htl-goa-soldout-rm\.webp$/,
    );
    await expect(card.locator("span", { hasText: "Sold out" })).toBeVisible();
  });

  test("an unmapped demo/growth SKU falls back to the gradient placeholder, never a broken image", async ({
    page,
  }) => {
    await page.route("**/buyer/v1/catalog*", async (route) => {
      const response = await route.fetch();
      const json = await response.json();
      const template = json.items[0];
      json.items = [
        ...json.items,
        { ...template, sku: "HTL-DEMO-FAKE", available_units: 3 },
      ];
      await route.fulfill({ response, json });
    });
    await page.reload();
    await expect(page.getByRole("heading", { name: "Stays in Goa" })).toBeVisible();
    await page.getByLabel("Refundable only").uncheck();

    const fakeCard = page.getByTestId("hotel-card-HTL-DEMO-FAKE");
    await expect(fakeCard).toBeVisible();
    await expect(fakeCard.locator("img")).toHaveCount(0);

    await page.unroute("**/buyer/v1/catalog*");
  });

  test("the details drawer shows the same curated image for a real hotel", async ({ page }) => {
    await page.getByTestId("hotel-card-HTL-GOA-SEA-DLX").getByRole("button", { name: "View details" }).click();
    const dialog = page.getByRole("dialog", { name: "Sea View Deluxe details" });
    await expect(dialog).toBeVisible();
    await expect(dialog.locator("img")).toHaveAttribute(
      "src",
      /\/images\/hotels\/htl-goa-sea-dlx\.webp$/,
    );
  });
});
