import { test, expect, type Page, type Route } from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';

const root = process.env.ATLAS_CI_DIR!;
const files = {
  acme: 'SYNTHETIC-acme-beacon.pdf',
  globex: 'SYNTHETIC-globex-comet.pdf',
};
async function credentials() {
  return JSON.parse(await fs.readFile(path.join(root, 'credentials.json'), 'utf8')) as Record<string, string>;
}
async function login(page: Page, tenant: 'acme' | 'globex') {
  const secret = (await credentials())[`${tenant}-demo`];
  await page.getByLabel('Demo account').selectOption(`${tenant}-demo`);
  await page.getByLabel('Password', { exact: true }).fill(secret);
  await page.getByRole('button', { name: 'Enter workspace' }).click();
  await expect(page.getByRole('heading', { name: 'A home for your knowledge.' })).toBeVisible();
  await expect(page.getByText('Simulated AI — deterministic local demo', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Loading documents')).not.toBeVisible();
}
async function logout(page: Page) {
  await page.getByRole('button', { name: 'Switch account / sign out', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Enter workspace' })).toBeVisible();
  await expect(page.locator('.answer-card')).toHaveCount(0);
}
async function screenshot(page: Page, name: string) {
  const password = page.getByLabel('Password', { exact: true });
  if (await password.count()) expect((await password.inputValue()) === '').toBeTruthy();
  await page.screenshot({ path: path.join(root, 'artifacts', name), fullPage: true,
    animations: 'disabled' });
}
async function overflow(page: Page) {
  const sizes = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth, content: document.documentElement.scrollWidth,
  }));
  expect(sizes.content <= sizes.viewport + 1).toBeTruthy();
}
function observe(page: Page) {
  const errors: string[] = [];
  let expectedStatus: number | null = null;
  let expectedPath = '';
  let expectedRemaining = 0;
  page.on('pageerror', () => errors.push('pageerror'));
  page.on('console', message => {
    if (message.type() !== 'error') return;
    if (expectedRemaining > 0 && message.text().includes(`status of ${expectedStatus}`)
        && message.location().url.endsWith(expectedPath)) {
      expectedRemaining--; return;
    }
    errors.push('console-error');
  });
  page.on('request', request => {
    const url = new URL(request.url());
    if (!['http://127.0.0.1:18030', 'null'].includes(url.origin)) errors.push('unexpected-origin');
  });
  return { errors, expectHttpError: (status: number, endpoint: string) => {
    expectedStatus = status; expectedPath = endpoint; expectedRemaining = 1;
  } };
}
async function ask(page: Page, question: string) {
  await page.getByRole('textbox', { name: 'Your question', exact: true }).fill(question);
  await page.getByRole('button', { name: 'Ask question', exact: true }).click();
}
async function ready(page: Page, filename: string) {
  const card = page.getByRole('article').filter({ has: page.getByRole('heading', { name: filename, exact: true }) });
  // App's genuine 2-second job polling drives this; no test-side API polling loop.
  await expect(card.getByText('Ready', { exact: true })).toBeVisible({ timeout: 100_000 });
}

test('fresh tenant libraries and authentication @empty', async ({ page }) => {
  const observed = observe(page);
  const { errors } = observed;
  await page.goto('/');
  await screenshot(page, 'login.png');
  await page.getByLabel('Password', { exact: true }).fill('intentionally-invalid-ci-password');
  observed.expectHttpError(401, '/api/v1/demo/session');
  const invalid = page.waitForResponse(response => response.url().endsWith('/api/v1/demo/session'));
  await page.getByRole('button', { name: 'Enter workspace' }).click();
  expect((await invalid).status()).toBe(401);
  await expect(page.getByRole('alert')).toHaveText('Account or password is incorrect.');
  for (const tenant of ['acme', 'globex'] as const) {
    await login(page, tenant);
    await expect(page.getByText('Your knowledge starts here', { exact: true })).toBeVisible();
    await expect(page.getByRole('group', { name: 'Document summary' })).toContainText('0');
    await logout(page);
  }
  expect(errors).toEqual([]);
});

test('real upload query citation and responsive dashboard @workflow', async ({ page }) => {
  const observed = observe(page);
  const { errors } = observed;
  await page.goto('/');
  await login(page, 'acme');
  await expect(page.getByRole('heading', { name: 'SYNTHETIC-acme-orion.pdf', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'SYNTHETIC-globex-nebula.pdf', exact: true })).toHaveCount(0);
  // Corrupt PDF goes to the real endpoint and must return a sanitized validation error.
  observed.expectHttpError(422, '/api/v1/documents');
  const invalid = page.waitForResponse(r => r.url().endsWith('/api/v1/documents') && r.request().method() === 'POST');
  await page.getByLabel('Select PDF').setInputFiles({ name: 'SYNTHETIC-invalid.pdf', mimeType: 'application/pdf', buffer: Buffer.from('not a PDF') });
  expect((await invalid).status()).toBe(422);
  await expect(page.getByRole('status')).toContainText('Please check your input');
  // Genuine file chooser (not a direct hidden-input substitute).
  const chooser = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: 'Choose PDF', exact: true }).click();
  await (await chooser).setFiles(path.join(root, 'fixtures', files.acme));
  await ready(page, files.acme);
  await screenshot(page, 'workspace-overview.png');
  await screenshot(page, 'document-library.png');
  await page.getByRole('button', { name: 'Ask AI', exact: true }).click();

  // Delay request dispatch only: the response always comes from the real server.
  // No route.fulfill(), fake JSON, provider calls or response substitution.
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const routeHandler = async (route: Route) => { await gate; await route.continue(); };
  await page.route('**/api/v1/query', routeHandler);
  await ask(page, 'What is the Beacon allowance?');
  await expect(page.getByRole('status')).toContainText('Finding the right passages');
  release();
  await expect(page.locator('.answer-text')).toContainText('419 USD');
  await expect(page.locator('.answer-text')).toContainText('Simulated AI');
  await page.unroute('**/api/v1/query', routeHandler);
  const citation = page.getByRole('button', { name: /^Download SYNTHETIC-acme-beacon\.pdf, page 2, citation \d+$/ });
  await expect(citation).toBeVisible();
  const download = page.waitForEvent('download');
  await citation.click();
  const downloaded = await (await download).path();
  expect(downloaded !== null).toBeTruthy();
  const expectedHash = JSON.parse(await fs.readFile(path.join(root, 'fixtures.json'), 'utf8'))[files.acme].sha256;
  const digest = createHash('sha256').update(await fs.readFile(downloaded!)).digest('hex');
  // Compare a boolean so failed assertions cannot echo downloaded data.
  expect(digest === expectedHash).toBeTruthy();
  await screenshot(page, 'answer-citations.png');
  await page.setViewportSize({ width: 390, height: 844 });
  await overflow(page);
  await screenshot(page, 'mobile-answer.png');
  await ask(page, 'What is the dental insurance reimbursement?');
  await expect(page.getByRole('heading', { name: 'No supporting sources returned', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Sources', exact: true })).toHaveCount(0);
  // Known lexical limitation: synonym-heavy wording must not be reported as semantic success.
  await ask(page, 'How much does Beacon provide for home office equipment?');
  await expect(page.getByRole('heading', { name: 'No supporting sources returned', exact: true })).toBeVisible();
  await page.getByRole('button', { name: /^Documents/ }).click();
  for (const [width, height, name] of [
    [1440, 900, 'document-library.png'], [1280, 800, 'desktop-1280.png'],
    [768, 1024, 'tablet-dashboard.png'], [390, 844, 'mobile-dashboard.png'],
  ] as const) {
    await page.setViewportSize({ width, height });
    await overflow(page);
    await expect(page.getByRole('button', { name: 'Switch account / sign out', exact: true })).toBeVisible();
    await screenshot(page, name);
  }
  await logout(page);
  await login(page, 'globex');
  await expect(page.getByRole('heading', { name: files.acme, exact: true })).toHaveCount(0);
  await expect(page.locator('.answer-card')).toHaveCount(0);
  // Real DataTransfer populated from the same synthetic fixture bytes.
  const bytes = [...await fs.readFile(path.join(root, 'fixtures', files.globex))];
  const transfer = await page.evaluateHandle(({ bytes, name }) => {
    const dt = new DataTransfer();
    dt.items.add(new File([new Uint8Array(bytes)], name, { type: 'application/pdf' }));
    return dt;
  }, { bytes, name: files.globex });
  await page.getByRole('region', { name: 'PDF upload' }).dispatchEvent('drop', { dataTransfer: transfer });
  await transfer.dispose();
  await ready(page, files.globex);
  await page.getByRole('button', { name: 'Ask AI', exact: true }).click();
  await ask(page, 'What is the Comet allowance?');
  await expect(page.locator('.answer-text')).toContainText('862 USD');
  await expect(page.getByRole('button', { name: /^Download SYNTHETIC-globex-comet\.pdf, page 2, citation \d+$/ })).toBeVisible();
  // Pending old-session query is cancelled on logout; release never injects a response.
  let releaseStale!: () => void;
  const staleGate = new Promise<void>(resolve => { releaseStale = resolve; });
  const pending = async (route: Route) => {
    await staleGate;
    try { await route.continue(); } catch { /* expected: logout aborts this request */ }
  };
  await page.route('**/api/v1/query', pending);
  await ask(page, 'What is the Comet allowance?');
  await expect(page.getByRole('status')).toContainText('Finding the right passages');
  await logout(page);
  await login(page, 'acme');
  releaseStale();
  await page.unroute('**/api/v1/query', pending);
  await expect(page.getByRole('heading', { name: files.globex, exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Ask AI', exact: true }).click();
  await expect(page.locator('.answer-card')).toHaveCount(0);
  await expect(page.locator('.ask-empty')).toBeVisible();
  await logout(page);
  // Frontend timer test; backend's expired JWT is checked separately over real HTTP.
  await page.clock.install();
  await login(page, 'acme');
  await page.clock.fastForward(900_001);
  await expect(page.getByRole('button', { name: 'Enter workspace' })).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('Your session has expired');
  await expect(page.locator('.document-card')).toHaveCount(0);
  await expect(page.locator('.answer-card')).toHaveCount(0);
  expect(errors).toEqual([]);
});
