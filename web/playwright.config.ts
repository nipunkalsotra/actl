import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:5173",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "npm run dev",
    url: "http://localhost:5173",
    reuseExistingServer: true,
    timeout: 30_000,
  },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 960 } },
    },
    {
      // Chromium-based mobile emulation (not iPhone/WebKit): this sandbox
      // has no WebKit system deps installable without sudo. What's under
      // test is the app's responsive breakpoint behaviour, not a WebKit
      // rendering quirk, so Chromium's own mobile device profile covers
      // it equally well.
      name: "mobile",
      use: { ...devices["Pixel 7"] },
    },
  ],
});
