import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  // Only on CI, and only one retry (one original attempt + one retry, two
  // total -- never three): a bounded mitigation for a GitHub Actions
  // shared-runner scheduling/interception flake (a just-closed modal's
  // exit animation racing a click, observed once and not reproducible
  // locally across 7 faithful replays against a genuinely fresh database).
  // This is not a way to hide a deterministic failure -- a real bug still
  // fails on the retry too. Never retries locally, where a failure should
  // always mean something real.
  retries: process.env.CI ? 1 : 0,
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
