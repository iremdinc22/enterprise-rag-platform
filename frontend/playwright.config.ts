import { defineConfig } from '@playwright/test';
import path from 'node:path';

const root = process.env.ATLAS_CI_DIR;
if (!root || process.env.GITHUB_ACTIONS !== 'true') {
  throw new Error('Explicit GitHub Actions validation directory required');
}
export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.e2e.ts',
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 180_000,
  globalTimeout: 8 * 60_000,
  expect: { timeout: 15_000 },
  outputDir: path.join(root, 'private-playwright-output'),
  reporter: process.argv.includes('--list') ? [['list']] : [['./e2e/safe-reporter.ts']],
  use: {
    baseURL: 'http://127.0.0.1:18030',
    browserName: 'chromium',
    headless: true,
    viewport: { width: 1440, height: 900 },
    actionTimeout: 15_000,
    navigationTimeout: 20_000,
    trace: 'off',
    video: 'off',
    screenshot: 'off',
    acceptDownloads: true,
  },
});
