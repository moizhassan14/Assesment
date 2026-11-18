import { test, expect, APIRequestContext, Page } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { Brand, Envelope, Report, Run } from "../src/app/core/models";
import AxeBuilder from "@axe-core/playwright";

const automatedProviders = process.env["RUN_FLASK_TEST_E2E"] === "1";
const artifactPrefix = automatedProviders ? "flask-test" : "live";
const accessibilityResults: unknown[] = [];
test.skip(
  process.env["ALLOW_ANALYSIS_CREATION"] !== "1" ||
    (process.env["RUN_LIVE_E2E"] !== "1" && !automatedProviders),
  "Analysis creation is disabled during routine verification. Explicitly opt in with ALLOW_ANALYSIS_CREATION=1 plus the selected provider mode. Prefer saved-reports.spec.ts.",
);
async function waitForRun(
  request: APIRequestContext,
  id: string,
): Promise<Run> {
  let last = "";
  for (let attempt = 0; attempt < 360; attempt++) {
    const response = await request.get(`/api/runs/${id}`);
    expect(response.ok()).toBe(true);
    const run = ((await response.json()) as Envelope<Run>).data;
    const current = `${run.status} ${run.progress.percent}% ${run.progress.current_node ?? ""}`;
    if (current !== last) {
      console.info(
        `${automatedProviders ? "Test" : "Live"} run ${id}: ${current}`,
      );
      last = current;
    }
    if (run.status !== "queued" && run.status !== "running") return run;
    await new Promise((resolve) => setTimeout(resolve, 2500));
  }
  throw new Error(`Live run ${id} did not terminate within 15 minutes.`);
}
async function screenshotSizes(page: Page, name: string) {
  for (const width of [1440, 1280, 768, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `test-results/${artifactPrefix}-${name}-${width}.png`,
      fullPage: true,
    });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  if (process.env["AUDIT_FULL_UI"] === "1") {
    const result = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
      .analyze();
    accessibilityResults.push({ view: name, violations: result.violations });
    writeFileSync(
      `test-results/${artifactPrefix}-accessibility.json`,
      JSON.stringify(accessibilityResults, null, 2),
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
        name,
      )
      .toEqual([]);
  }
}
test("Flask lifecycle: create brand, discovery, reports, remeasure, and comparison", async ({
  page,
  request,
}) => {
  test.setTimeout(2100000);
  const browserErrors: string[] = [];
  page.on("pageerror", (e) => browserErrors.push(e.message));
  console.info(
    automatedProviders
      ? "AUTOMATED TEST PROVIDERS: real Flask/DAG, test doubles at external boundaries."
      : "LIVE PROVIDERS: real Flask/DAG and external APIs.",
  );
  const health = await request.get("/api/health");
  expect(health.ok()).toBe(true);
  const checks = (await health.json()).data.checks;
  expect(checks.dataforseo_credentials).toBe("configured");
  if (!automatedProviders) expect(checks.llm_credentials).toBe("configured");
  const existing = await request.get("/api/brands?q=linear.app");
  const brands = ((await existing.json()) as Envelope<Brand[]>).data;
  let brand = brands.find((b) => b.domain === "linear.app");
  if (!brand) {
    await page.goto("/brands/new");
    await page.getByLabel("Brand name", { exact: true }).fill("Linear");
    await page.getByLabel("Website or domain").fill("linear.app");
    await page
      .getByLabel(/What does your brand do/)
      .fill(
        "Issue tracking and project management software for product development teams.",
      );
    await page
      .getByLabel("Seed keywords", { exact: true })
      .fill("issue tracking software, project management software");
    await page
      .getByLabel(/Research question/)
      .fill(
        "How does Linear show up in AI answers and organic search for issue tracking software?",
      );
    await page
      .getByRole("button", { name: "Add competitor", exact: true })
      .click();
    await page.getByLabel("Competitor 1 name").fill("Asana");
    await page.getByLabel("Domain", { exact: true }).fill("asana.com");
    const created = page.waitForResponse(
      (r) => r.url().endsWith("/api/brands") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Create brand" }).click();
    const response = await created;
    expect(response.status()).toBe(201);
    brand = ((await response.json()) as Envelope<Brand>).data;
    await expect(page).toHaveURL(new RegExp(`/brands/${brand.id}$`));
  } else {
    await page.goto(`/brands/${brand.id}`);
  }
  const brandId = brand.id;
  await expect(
    page.getByRole("heading", { name: "Linear", exact: true }),
  ).toBeVisible();
  await screenshotSizes(page, "brand");
  const pending = page.waitForResponse(
    (r) =>
      r.url().endsWith(`/brands/${brandId}/runs`) &&
      r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Start analysis", exact: true })
    .click();
  const accepted = await pending;
  expect(accepted.status()).toBe(202);
  const firstId = (await accepted.json()).data.run_id as string;
  await expect(page).toHaveURL(new RegExp(`/runs/${firstId}/progress`));
  await expect(
    page.getByRole("heading", { name: "The research pipeline" }),
  ).toBeVisible();
  await screenshotSizes(page, "progress");
  const run = await waitForRun(request, firstId);
  expect(["success", "partial_success"]).toContain(run.status);
  const reportResponse = await request.get(`/api/runs/${firstId}/report`);
  const report = ((await reportResponse.json()) as Envelope<Report>).data;
  expect(report.is_final).toBe(true);
  expect(report.observations.some((o) => o.status === "measured")).toBe(true);
  if (process.env["EXPECT_PARTIAL"] === "1") {
    expect(run.status).toBe("partial_success");
    expect(report.run.headline?.ai_visibility_rate).toBeNull();
    expect(report.visibility?.chatgpt.failed).toBeGreaterThan(0);
    expect(
      report.observations
        .filter((o) => o.surface === "chatgpt")
        .every((o) => o.status === "failed"),
    ).toBe(true);
  }
  mkdirSync("test-results", { recursive: true });
  writeFileSync(
    `test-results/${artifactPrefix}-discovery-report.json`,
    JSON.stringify(report, null, 2),
  );
  for (const tab of ["overview", "opportunities", "recommendations"]) {
    await page.goto(`/runs/${firstId}/${tab}`);
    await expect(
      page.getByRole("heading", { name: "Linear / Visibility report" }),
    ).toBeVisible();
    await expect(page.locator(".loading-state")).toHaveCount(0);
    if (tab === "opportunities" && report.opportunities.length) {
      await expect(page.locator("details.opportunity")).toHaveCount(
        report.opportunities.length,
      );
      await page
        .locator("details.opportunity")
        .first()
        .locator("summary")
        .click();
      await expect(
        page
          .locator("details.opportunity")
          .first()
          .getByText("Google Ads paid competition", { exact: true }),
      ).toBeVisible();
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
    if (tab === "recommendations" && report.recommendations.length) {
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
    await screenshotSizes(page, tab);
  }
  await page.goto(`/brands/${brandId}`);
  await page.getByLabel("Analysis type").selectOption("remeasure");
  const recheck = page.waitForResponse(
    (r) =>
      r.url().endsWith(`/brands/${brandId}/runs`) &&
      r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Re-check visibility", exact: true })
    .click();
  const second = await recheck;
  expect(second.status()).toBe(202);
  const secondId = (await second.json()).data.run_id as string;
  expect(secondId).not.toBe(firstId);
  const secondRun = await waitForRun(request, secondId);
  expect(["success", "partial_success"]).toContain(secondRun.status);
  expect(secondRun.mode).toBe("remeasure");
  await page.goto(`/brands/${brandId}/history`);
  await expect(
    page.getByRole("heading", { name: "Saved analyses" }),
  ).toBeVisible();
  await expect(
    page.getByText("QUERIES IMPROVED", { exact: true }),
  ).toBeVisible();
  await screenshotSizes(page, "history");
  const compare = await request.get(`/api/brands/${brandId}/compare`);
  expect(compare.status()).toBe(200);
  writeFileSync(
    `test-results/${artifactPrefix}-comparison.json`,
    JSON.stringify((await compare.json()).data, null, 2),
  );
  expect((await request.get(`/api/runs/${firstId}/report`)).status()).toBe(200);
  await page.goto("/");
  await expect(
    page
      .getByRole("table")
      .getByRole("link", { name: "Open Linear", exact: true }),
  ).toBeVisible();
  await screenshotSizes(page, "portfolio");
  expect(browserErrors).toEqual([]);
  console.info(
    `Verified ${automatedProviders ? "test-provider" : "live"} brand ${brandId}; discovery ${firstId}; remeasure ${secondId}.`,
  );
});
