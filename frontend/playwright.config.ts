import { defineConfig, devices } from '@playwright/test'

/**
 * Admin console smoke suite. Runs against servers that are already up -- the local preview
 * (frontend :3000, backend per NEXT_PUBLIC_API_URL) or the ones the CI job starts -- and seeds
 * its own throwaway accounts through scripts/e2e_seed.py, which refuses any hosted database.
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['list']] : 'list',
  globalSetup: './e2e/global-setup.ts',
  globalTeardown: './e2e/global-teardown.ts',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:3000',
    trace: 'retain-on-failure',
  },
  projects: [{
    name: 'chromium',
    use: {
      ...devices['Desktop Chrome'],
      // channel 'chromium': the full browser in its new headless mode, so only one browser
      // download is needed rather than a second headless-shell build. PW_LOCAL_CHROME_PATH
      // is a per-machine escape hatch for a chrome.exe already on disk (unset in CI and by
      // default -- never commit a machine-specific path here).
      ...(process.env.PW_LOCAL_CHROME_PATH
        ? { channel: undefined, launchOptions: { executablePath: process.env.PW_LOCAL_CHROME_PATH } }
        : { channel: 'chromium' }),
    },
  }],
})
