import { test, expect } from "@playwright/test";

test("home page loads and shows NaviGraph", async ({ page }) => {
  const response = await page.goto("/");

  expect(response?.status()).toBe(200);
  await expect(page.locator("body")).toContainText("NaviGraph");
});
