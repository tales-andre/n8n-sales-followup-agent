// Takes the screenshots in docs/ from the running demo stack.
//   docker compose up -d && npm install && npm run screenshots
// Uses the Microsoft Edge (or Chrome) already installed on the machine via playwright-core.
// It resets the demo data, runs the daily workflow, approves one draft by clicking the
// real button on the approval page, then captures Mailpit and the n8n canvas.
import { chromium } from 'playwright-core';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DOCS = path.join(ROOT, 'docs');
const N8N = process.env.N8N_URL || 'http://localhost:5678';
const MAILPIT = process.env.MAILPIT_URL || 'http://localhost:8025';
// Local demo login for the n8n editor only (created on first run of this script).
const OWNER = { email: 'owner@cedarridge-supply.example', password: 'Demo-only-Passw0rd', firstName: 'Demo', lastName: 'Owner' };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const sql = (q) => execFileSync('docker', ['compose', 'exec', '-T', 'postgres', 'psql', '-U', 'sales', '-d', 'sales', '-Atc', q], { cwd: ROOT }).toString();

function browserPath() {
  const candidates = [
    process.env.BROWSER_PATH,
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    '/usr/bin/microsoft-edge', '/usr/bin/google-chrome', '/usr/bin/chromium',
  ].filter(Boolean);
  const found = candidates.find((p) => existsSync(p));
  if (!found) throw new Error('No Edge/Chrome found. Set BROWSER_PATH.');
  return found;
}

mkdirSync(DOCS, { recursive: true });

// 1. fresh demo data and one daily run
sql('TRUNCATE activity_log, followups RESTART IDENTITY; UPDATE accounts SET last_contacted_at = NULL;');
await fetch(MAILPIT + '/api/v1/messages', { method: 'DELETE' });
const run = await fetch(N8N + '/webhook/run-followups', { method: 'POST' });
if (!run.ok) throw new Error('daily run failed: ' + run.status);

const browser = await chromium.launch({ executablePath: browserPath(), headless: true });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, deviceScaleFactor: 1 });
const page = await ctx.newPage();

// 2. approval page (full page) + the morning summary email
await page.goto(N8N + '/webhook/approvals');
await page.waitForSelector('text=Follow-ups waiting for approval');
await page.screenshot({ path: path.join(DOCS, 'approval-queue.png'), fullPage: false });
console.log('docs/approval-queue.png');

// 3. edit and approve the first draft with the real buttons
const card = page.locator('.card', { hasText: 'Harbor View Deli' });
await card.locator('input[name=subject]').fill('Quick check-in from Cedar Ridge Supply');
await card.locator('button[value=approve]').click();
await page.waitForSelector('h1:has-text("Sent")');
await page.screenshot({ path: path.join(DOCS, 'approved-sent.png'), clip: { x: 250, y: 0, width: 900, height: 180 } });
console.log('docs/approved-sent.png');

// 4. the email as received (Mailpit inbox)
await sleep(1500);
const list = await (await fetch(MAILPIT + '/api/v1/messages')).json();
const sent = list.messages.find((m) => m.To[0].Address === 'sam@harborviewdeli.example');
const digest = list.messages.find((m) => m.To[0].Address.startsWith('manager@'));
await page.setViewportSize({ width: 1400, height: 820 });
await page.goto(MAILPIT + '/view/' + sent.ID);
await page.waitForTimeout(2500);
await page.screenshot({ path: path.join(DOCS, 'mailpit-received-email.png') });
console.log('docs/mailpit-received-email.png');
await page.goto(MAILPIT + '/view/' + digest.ID);
await page.waitForTimeout(2500);
await page.screenshot({ path: path.join(DOCS, 'mailpit-morning-summary.png') });
console.log('docs/mailpit-morning-summary.png');

// 5. an error alert (bad address on purpose: Bluefin Bistro's email is mistyped in the sample data)
await page.goto(N8N + '/webhook/approvals');
await page.locator('.card', { hasText: 'Bluefin Bistro' }).locator('button[value=approve]').click();
await page.waitForSelector('h1:has-text("Not sent")');
await sleep(1500);
const list2 = await (await fetch(MAILPIT + '/api/v1/messages')).json();
const alert = list2.messages.find((m) => m.To[0].Address.startsWith('alerts@'));
await page.goto(MAILPIT + '/view/' + alert.ID);
await page.waitForTimeout(2500);
await page.screenshot({ path: path.join(DOCS, 'mailpit-alert.png') });
console.log('docs/mailpit-alert.png');

// 6. n8n canvas (needs an editor login; creates the local demo owner the first time)
// sharper images for the canvas (small node labels)
const ctx2 = await browser.newContext({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 2 });
const canvas = await ctx2.newPage();
const setup = await canvas.request.post(N8N + '/rest/owner/setup', { data: OWNER });
if (!setup.ok()) {
  const login = await canvas.request.post(N8N + '/rest/login', { data: { emailOrLdapLoginId: OWNER.email, password: OWNER.password } });
  if (!login.ok()) throw new Error('n8n login failed: ' + login.status() + ' ' + (await login.text()));
}
for (const [id, file] of [['wfDailyDrafts001', 'n8n-canvas-daily-drafts.png'], ['wfApprovalQueue1', 'n8n-canvas-approval-queue.png'], ['wfErrorAlert0001', 'n8n-canvas-error-alert.png']]) {
  await canvas.goto(N8N + '/workflow/' + id);
  await canvas.waitForSelector('.vue-flow__node', { timeout: 60000 });
  await canvas.waitForTimeout(2500);
  // close any onboarding/what's-new popups
  for (const label of ['Close', 'Got it', 'Skip', 'Dismiss']) {
    const b = canvas.getByRole('button', { name: label });
    if (await b.count()) await b.first().click().catch(() => {});
  }
  await canvas.keyboard.press('Escape');
  await canvas.locator('.vue-flow__pane').click({ position: { x: 5, y: 5 } }).catch(() => {});
  await canvas.keyboard.press('1'); // zoom to fit
  await canvas.waitForTimeout(1500);
  await canvas.screenshot({ path: path.join(DOCS, file) });
  console.log('docs/' + file);
}

await browser.close();
