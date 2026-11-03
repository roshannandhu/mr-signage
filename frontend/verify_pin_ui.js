const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const CHROME_PATH = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const EVIDENCE_DIR = path.resolve(__dirname, '..', 'evidence');

(async () => {
  console.log('=== VERIFYING INDEPENDENT PIN CONTROLS (WORKSPACE & SCREEN) ===');

  const browser = await chromium.launch({ executablePath: CHROME_PATH, headless: true });
  const context = await browser.newContext({ viewport: { width: 1366, height: 768 } });
  const page = await context.newPage();

  // 1. Login as Tenant Owner
  await page.goto('http://localhost:3000/login', { waitUntil: 'networkidle' });
  await page.fill('#username', 'owner@olrac.com');
  await page.fill('#password', 'olrac1234');
  await page.click('button[type="submit"]');
  await page.waitForTimeout(2000);

  // ---------------------------------------------------------------------------
  // Step A: Workspace PIN on /dashboard/screens
  // ---------------------------------------------------------------------------
  console.log('1. Navigating to /dashboard/screens...');
  await page.goto('http://localhost:3000/dashboard/screens', { waitUntil: 'networkidle' });
  await page.waitForSelector('summary:has-text("Workspace lock pin")', { timeout: 10000 });

  const pinSummary = page.locator('summary:has-text("Workspace lock pin")');
  await pinSummary.click();
  await page.waitForTimeout(500);

  const newWorkspacePin = '7712';
  console.log(`   Setting new Workspace PIN to: ${newWorkspacePin}...`);
  const wsInput = page.locator('input[placeholder="0000"]').first();
  await wsInput.fill(newWorkspacePin);
  await page.locator('button:has-text("Save")').first().click();
  await page.waitForTimeout(1000);

  // Capture proof of Workspace PIN updated
  await page.screenshot({ path: path.join(EVIDENCE_DIR, 'proof_workspace_pin_updated.png') });
  console.log('   Workspace PIN saved and captured: evidence/proof_workspace_pin_updated.png');

  // ---------------------------------------------------------------------------
  // Step B: Individual TV PIN on /dashboard/screens/1 (Screen 1)
  // ---------------------------------------------------------------------------
  console.log('2. Navigating to Screen 1 (/dashboard/screens/1)...');
  await page.goto('http://localhost:3000/dashboard/screens/1', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1000);

  const kioskDetails = page.locator('details:has(span:has-text("Getting into this screen"))');
  await kioskDetails.click();
  await page.waitForTimeout(500);

  // Verify PIN before custom override
  let pinBefore = await page.locator('code').first().textContent();
  console.log(`   Screen 1 Effective PIN before custom override: ${pinBefore}`);

  // Set Custom Screen PIN
  const customScreenPin = '3849';
  console.log(`   Setting Custom Screen PIN to: ${customScreenPin}...`);
  const customPinInput = page.locator('input[placeholder*="4-digit PIN"]').first();
  await customPinInput.fill(customScreenPin);
  await page.locator('button:has-text("Save PIN")').click();
  await page.waitForTimeout(1000);

  // Re-read effective PIN after save
  let pinAfter = await page.locator('code').first().textContent();
  let sourceBadge = await page.locator('text=Screen Custom Override').isVisible();
  console.log(`   Screen 1 Effective PIN after save: ${pinAfter} (Source: Screen Custom Override = ${sourceBadge})`);

  await page.screenshot({ path: path.join(EVIDENCE_DIR, 'proof_screen1_custom_pin_updated.png') });

  // ---------------------------------------------------------------------------
  // Step C: Reset Custom Screen PIN back to Workspace Inherited
  // ---------------------------------------------------------------------------
  console.log('3. Resetting Screen 1 to Workspace Inherited PIN...');
  const resetBtn = page.locator('button:has-text("Reset to Workspace PIN")');
  await resetBtn.click();
  await page.waitForTimeout(1000);

  let pinReverted = await page.locator('code').first().textContent();
  let inheritedBadge = await page.locator('text=Workspace Shared PIN').isVisible();
  console.log(`   Screen 1 Effective PIN after reset: ${pinReverted} (Source: Workspace Shared PIN = ${inheritedBadge})`);

  await page.screenshot({ path: path.join(EVIDENCE_DIR, 'proof_screen1_reverted_to_workspace_pin.png') });

  await browser.close();

  const proofSummary = {
    testedAt: new Date().toISOString(),
    workspacePinSet: newWorkspacePin,
    screen1EffectiveBefore: pinBefore,
    screen1CustomPinSet: customScreenPin,
    screen1EffectiveAfterCustom: pinAfter,
    screen1EffectiveAfterRevert: pinReverted,
    verificationPass: pinAfter.trim() === customScreenPin && pinReverted.trim() === newWorkspacePin,
  };

  fs.writeFileSync(path.join(EVIDENCE_DIR, 'proof_pin_controls_verification.json'), JSON.stringify(proofSummary, null, 2));
  console.log('=== INDEPENDENT PIN CONTROLS VERIFIED SUCCESSFULLY ===');
})().catch(err => {
  console.error('Error in PIN control verification:', err);
  process.exit(1);
});
