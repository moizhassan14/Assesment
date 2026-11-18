import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdirSync, writeFileSync } from "node:fs";
import { Brand, Envelope, Report, Run, RunIssue } from "../src/app/core/models";

test.skip(
  process.env["RUN_PERSISTED_E2E"] !== "1",
  "Use RUN_PERSISTED_E2E=1 with APP_URL pointing at an existing backend. This test only reads saved data.",
);
test("saved reports render responsively without creating analyses or calling providers", async ({
  page,
  request,
}) => {
  test.setTimeout(240000);
  const mutations: string[] = [];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/**", async (route) => {
    const method = route.request().method();
    if (method !== "GET" && method !== "HEAD") {
      mutations.push(`${method} ${new URL(route.request().url()).pathname}`);
      await route.abort();
      return;
    }
    await route.continue();
  });
  const response = await request.get("/api/brands?limit=100");
  expect(response.ok()).toBe(true);
  const brands = ((await response.json()) as Envelope<Brand[]>).data;
  const brand = process.env["PERSISTED_BRAND_ID"]
    ? brands.find((item) => item.id === process.env["PERSISTED_BRAND_ID"])
    : brands.find((item) => item.latest_run);
  expect(
    brand,
    "At least one persisted brand and analysis is required.",
  ).toBeTruthy();
  const runId = process.env["PERSISTED_RUN_ID"] ?? brand!.latest_run!.id;
  const beforeResponse = await request.get(
    `/api/brands/${brand!.id}/runs?limit=100`,
  );
  const before = (await beforeResponse.json()) as { meta: { total: number } };
  const reportResponse = await request.get(`/api/runs/${runId}/report`);
  expect(reportResponse.ok()).toBe(true);
  const report = ((await reportResponse.json()) as Envelope<Report>).data;
  expect(
    report.is_final,
    "Use a saved terminal report; do not start new work for this test.",
  ).toBe(true);
  const audits: unknown[] = [];
  const views = [
    { name: "portfolio", path: "/" },
    { name: "brand", path: `/brands/${brand!.id}` },
    { name: "pipeline", path: `/runs/${runId}/progress` },
    { name: "overview", path: `/runs/${runId}/overview` },
    { name: "opportunities", path: `/runs/${runId}/opportunities` },
    { name: "recommendations", path: `/runs/${runId}/recommendations` },
    { name: "history", path: `/brands/${brand!.id}/history` },
  ];
  mkdirSync("test-results", { recursive: true });
  for (const view of views) {
    await page.goto(view.path);
    await expect(page.locator("main h1")).toBeVisible();
    await expect(page.locator(".loading-state")).toHaveCount(0);
    if (view.name === "opportunities" && report.opportunities.length) {
      await expect(page.locator("details.opportunity")).toHaveCount(
        report.opportunities.length,
      );
      await page
        .locator("details.opportunity")
        .first()
        .locator("summary")
        .click();
      await page
        .getByRole("searchbox", { name: "Search opportunities" })
        .fill("no matching query xzqw");
      await expect(
        page.getByRole("heading", {
          name: "No opportunities match these filters",
        }),
      ).toBeVisible();
      await page
        .getByRole("searchbox", { name: "Search opportunities" })
        .fill("");
    }
    if (view.name === "recommendations" && report.recommendations.length) {
      await expect(page.locator(".recommendation-card")).toHaveCount(
        report.recommendations.length,
      );
      await page
        .getByRole("button", { name: "Copy content brief" })
        .first()
        .click();
      await expect(
        page.getByRole("status").filter({ hasText: /copied|Select and copy/ }),
      ).toBeVisible();
    }
    if (view.name === "overview" && report.run.status === "failed") {
      await expect(
        page.getByRole("heading", { name: "No usable visibility evidence" }),
      ).toBeVisible();
    }
    if (view.name === "overview" && report.run.status === "partial_success") {
      await expect(
        page.getByText("Useful evidence. An incomplete picture."),
      ).toBeVisible();
      const failedQueries = report.observations.filter(
        (o) => o.status === "failed",
      ).length;
      if (failedQueries)
        await expect(
          page.getByRole("cell", { name: "Unknown · measurement failed" }),
        ).toHaveCount(failedQueries);
    }
    for (const width of [1440, 1280, 768, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
        `${view.name} overflow at ${width}px`,
      ).toBe(true);
      await page.screenshot({
        path: `test-results/saved-${view.name}-${width}.png`,
        fullPage: true,
      });
    }
    await page.setViewportSize({ width: 1280, height: 1000 });
    if (process.env["AUDIT_FULL_UI"] === "1") {
      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
        .analyze();
      audits.push({ view: view.name, violations: results.violations });
      writeFileSync(
        "test-results/saved-accessibility.json",
        JSON.stringify(audits, null, 2),
      );
      expect
        .soft(
          results.violations.map((v) => ({
            id: v.id,
            nodes: v.nodes.map((n) => ({
              target: n.target,
              summary: n.failureSummary,
            })),
          })),
          view.name,
        )
        .toEqual([]);
    }
  }
  const after = await request.get(`/api/brands/${brand!.id}/runs?limit=100`);
  expect((await after.json()).meta.total).toBe(before.meta.total);
  expect(
    mutations,
    "UI verification must never create work or call provider APIs.",
  ).toEqual([]);
  expect(errors).toEqual([]);
  console.info(
    `Read-only verification: ${report.run.status}, ${report.opportunities.length} opportunities, ${report.recommendations.length} recommendations, unchanged run count ${before.meta.total}.`,
  );
});

test("partial-success presentation preserves saved evidence and marks failed measurements unknown", async ({
  page,
  request,
}) => {
  test.setTimeout(90000);
  const brands = (
    (await (await request.get("/api/brands?limit=100")).json()) as Envelope<
      Brand[]
    >
  ).data;
  const brand = brands.find(
    (item) =>
      item.latest_run?.status === "success" ||
      item.latest_run?.status === "partial_success",
  );
  test.skip(
    !brand,
    "No saved report with evidence is available for a test-only partial-state fixture.",
  );
  const id = brand!.latest_run!.id;
  const source = (
    (await (
      await request.get(`/api/runs/${id}/report`)
    ).json()) as Envelope<Report>
  ).data;
  test.skip(
    !source.visibility,
    "A saved report with measured evidence is required.",
  );
  const run = (
    (await (await request.get(`/api/runs/${id}`)).json()) as Envelope<Run>
  ).data;
  // Modify only a browser-test response derived from an already persisted report.
  // No backend state changes, new analyses, external calls, or frontend score calculation.
  const report = structuredClone(source);
  const issue: RunIssue = {
    node: "retrieve_ai_answers",
    code: "provider_rate_limited",
    message: "Automated-test provider failure",
    retryable: true,
    provider: "dataforseo",
    occurred_at: run.finished_at ?? run.created_at,
  };
  report.run.status = "partial_success";
  report.errors = [issue];
  run.status = "partial_success";
  run.errors = [issue];
  const failed = report.observations.filter((o) => o.surface === "chatgpt");
  for (const observation of failed) {
    observation.status = "failed";
    observation.brand = { mentioned: null, cited: null, rank: null, url: null };
    observation.error = issue;
  }
  report.visibility!.chatgpt = {
    measured: 0,
    failed: failed.length,
    brand_mentioned: 0,
    brand_cited: 0,
  };
  if (report.run.headline) report.run.headline.ai_visibility_rate = null;
  for (const opportunity of report.opportunities) opportunity.is_partial = true;
  await page.route("**/api/**", async (route) => {
    if (!["GET", "HEAD"].includes(route.request().method())) {
      await route.abort();
      return;
    }
    await route.continue();
  });
  await page.route(`**/api/runs/${id}`, (route) =>
    route.fulfill({ json: { data: run } }),
  );
  await page.route(`**/api/runs/${id}/report`, (route) =>
    route.fulfill({ json: { data: report } }),
  );
  await page.goto(`/runs/${id}/overview`);
  await expect(
    page.getByText("Useful evidence. An incomplete picture."),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "Unknown · measurement failed" }),
  ).toHaveCount(failed.length);
  await expect(
    page.locator('app-metric[label="AI visibility"] strong'),
  ).toHaveText("—");
  await expect(
    page.locator('app-metric[label="Content opportunities"] strong'),
  ).toHaveText(String(report.opportunities.length));
  for (const width of [1280, 768, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `test-results/fixture-partial-${width}.png`,
      fullPage: true,
    });
  }
  const audit = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(
    audit.violations.map((v) => ({
      id: v.id,
      targets: v.nodes.map((n) => n.target),
    })),
  ).toEqual([]);
});
