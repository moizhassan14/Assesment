import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { writeFileSync, mkdirSync } from "node:fs";
test("onboarding and setup meet automated WCAG A/AA checks", async ({
  page,
}) => {
  await page.route("**/api/brands?*", (route) =>
    route.fulfill({
      json: { data: [], meta: { total: 0, offset: 0, limit: 12 } },
    }),
  );
  await page.route("**/api/config", (route) =>
    route.fulfill({
      json: {
        data: {
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
        },
      },
    }),
  );
  const results: unknown[] = [];
  for (const route of ["/", "/brands/new"]) {
    for (const width of [1280, 768, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.goto(route);
      await expect(page.locator(".loading-state")).toHaveCount(0);
      const result = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
        .analyze();
      results.push({ route, width, violations: result.violations });
      mkdirSync("test-results", { recursive: true });
      writeFileSync(
        "test-results/accessibility.json",
        JSON.stringify(results, null, 2),
      );
      expect
        .soft(
          result.violations.map((v) => ({
            id: v.id,
            nodes: v.nodes.map((n) => ({
              target: n.target,
              summary: n.failureSummary,
            })),
          })),
          `${route} at ${width}px`,
        )
        .toEqual([]);
    }
  }
});
