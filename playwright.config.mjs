import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/browser",
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:4175",
    browserName: "chromium",
    headless: true,
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH }
      : {},
  },
  webServer: {
    command: "python3 -m http.server 4175 --bind 127.0.0.1",
    url: "http://127.0.0.1:4175/index.html",
    reuseExistingServer: !process.env.CI,
  },
});
