import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "screens.spec.ts",
  fullyParallel: true,    // each shot is independent; running in parallel cuts the suite to ~5s
  workers: 4,
  retries: 1,
  timeout: 30_000,
  reporter: [["list"]],
  use: {
    // browser2zen's design viewport is 760x580. We add a small margin so
    // the body's drop-shadow is fully visible in the capture.
    viewport: { width: 800, height: 620 },
    deviceScaleFactor: 2,      // retina output, matches the existing PNGs
    colorScheme: "dark",
  },
  projects: [
    {
      name: "chromium",
      use: {
        // Don't spread devices["Desktop Chrome"]: it forces a 1280x720
        // viewport and DPR=1, which overrides our settings above.
        browserName: "chromium",
      },
    },
  ],
});
