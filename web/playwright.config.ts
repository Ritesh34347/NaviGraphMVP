import { defineConfig } from "@playwright/test";

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  reporter: "list",
  use: {
    baseURL,
  },
  // REAL BUG, found live on this repo's very first real GitHub Actions run:
  // there was no `webServer` config at all, so `playwright test` on a clean
  // CI runner (nothing else started) always failed immediately with
  // ERR_CONNECTION_REFUSED at localhost:3000. Locally this was never
  // noticed because a docker-compose `web` service (or a manually-run
  // `next dev`) was already up on port 3000 whenever this was run by hand.
  // `reuseExistingServer` keeps that manual/dev-compose workflow unchanged
  // while giving CI a real, self-contained server to test against.
  webServer: {
    command: "npm run dev",
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
