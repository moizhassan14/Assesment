import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  timeout: 60000,
  expect: { timeout: 12000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    actionTimeout: 15000,
    navigationTimeout: 30000,
    baseURL: process.env["APP_URL"] ?? "http://127.0.0.1:4200",
    headless: true,
    channel: "chrome",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: process.env["APP_URL"]
    ? undefined
    : {
        command: "npm start",
        url: "http://127.0.0.1:4200",
        reuseExistingServer: true,
        timeout: 60000,
      },
});
