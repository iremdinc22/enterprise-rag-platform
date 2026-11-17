import type { FullResult, Reporter, TestCase, TestResult } from '@playwright/test/reporter';
import fs from 'node:fs';
import path from 'node:path';

// Never serialize TestResult errors, steps, attachments, headers or API bodies.
export default class SafeReporter implements Reporter {
  private rows: { name: string; status: string; duration_ms: number; source_line?: number }[] = [];
  onTestEnd(test: TestCase, result: TestResult) {
    const name = test.title.includes('@empty') ? 'empty-authentication' : 'dashboard-workflow';
    const line = result.error?.stack?.match(/demo\.e2e\.ts:(\d+):\d+/)?.[1];
    this.rows.push({ name, status: result.status, duration_ms: result.duration,
      ...(line ? { source_line: Number(line) } : {}) });
    console.log(`${name}: ${result.status}`);
  }
  onError() { console.log('Browser infrastructure error; raw details withheld'); }
  onEnd(result: FullResult) {
    const phase = process.env.ATLAS_BROWSER_PHASE;
    if (phase !== 'empty' && phase !== 'workflow') throw new Error('Invalid report phase');
    const root = process.env.ATLAS_CI_DIR!;
    fs.writeFileSync(path.join(root, 'artifacts', `browser-${phase}.json`), JSON.stringify({
      status: result.status, tests: this.rows,
      passed: this.rows.filter(row => row.status === 'passed').length,
      failed: this.rows.filter(row => row.status !== 'passed').length,
    }, null, 2));
  }
}
