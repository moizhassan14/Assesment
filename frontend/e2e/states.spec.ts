import { test, expect } from "@playwright/test";

// Contract fixtures are used only here, never in the running app.
const config = {
  markets: [
    {
      location_code: 2840,
      language_code: "en",
      location_name: "United States",
      language_name: "English",
    },
  ],
  limits: {
    max_competitors: 5,
    max_seed_keywords: 5,
    question_max_length: 400,
  },
  data_mode: "live",
};
test("first-time onboarding, validation, keyboard navigation, and responsive layouts", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/brands?*", (route) =>
    route.fulfill({
      json: { data: [], meta: { total: 0, offset: 0, limit: 12 } },
    }),
  );
  await page.route("**/api/config", (route) =>
    route.fulfill({ json: { data: config } }),
  );
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Your next insight starts here" }),
  ).toBeVisible();
  for (const width of [1440, 1280, 768, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await expect(page.locator("h1")).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `test-results/overview-empty-${width}.png`,
      fullPage: true,
    });
  }
  await page.getByRole("link", { name: "Add your first brand" }).click();
  await expect(
    page.getByRole("heading", { name: "Make your brand the starting point." }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Create brand" }).click();
  await expect(
    page.getByText("This field is required.", { exact: true }).first(),
  ).toBeVisible();
  await expect(page.getByLabel("Brand name", { exact: true })).toBeFocused();
  await page.getByLabel("Brand name", { exact: true }).fill("A real brand");
  await page.getByLabel("Website or domain").fill("invalid");
  await page.getByLabel("Seed keywords", { exact: true }).fill("software");
  await page.getByRole("button", { name: "Create brand" }).click();
  await expect(
    page.getByText("Enter a valid website or domain, such as example.com."),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Add competitor", exact: true })
    .click();
  await expect(page.getByLabel("Competitor 1 name")).toBeVisible();
  for (const width of [1440, 1280, 768, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `test-results/brand-form-${width}.png`,
      fullPage: true,
    });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Toggle navigation" }).click();
  await expect(page.locator(".sidebar")).toHaveClass(/is-open/);
  await page
    .locator(".sidebar")
    .getByRole("link", { name: "Brand overview" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Your next insight starts here" }),
  ).toBeVisible();
  await expect(page.locator(".sidebar")).not.toHaveClass(/is-open/);
  expect(errors).toEqual([]);
});
test("network recovery and invalid routes are designed states", async ({
  page,
}) => {
  await page.route("**/api/client-errors", (route) =>
    route.fulfill({ status: 202, json: { data: { id: "diagnostic" } } }),
  );
  await page.route("**/api/brands?*", (route) =>
    route.fulfill({
      status: 503,
      json: {
        error: {
          code: "service_unavailable",
          message: "Private traceback must not be shown",
          details: [],
          request_id: "qa-reference",
        },
      },
    }),
  );
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Let’s get you reconnected" }),
  ).toBeVisible();
  await expect(page.getByText("Reference: qa-reference")).toBeVisible();
  await expect(
    page.getByText("Private traceback must not be shown"),
  ).toHaveCount(0);
  await page.route("**/api/brands?*", (route) =>
    route.fulfill({
      json: { data: [], meta: { total: 0, offset: 0, limit: 12 } },
    }),
  );
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(
    page.getByRole("heading", { name: "Your next insight starts here" }),
  ).toBeVisible();
  await page.goto("/missing-page");
  await expect(
    page.getByRole("heading", { name: "This page is off the map" }),
  ).toBeVisible();
});
