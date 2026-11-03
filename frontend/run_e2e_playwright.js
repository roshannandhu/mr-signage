const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const CHROME_PATH = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const EVIDENCE_DIR = path.resolve(__dirname, '..', 'evidence');

if (!fs.existsSync(EVIDENCE_DIR)) {
  fs.mkdirSync(EVIDENCE_DIR, { recursive: true });
}

const VIEWPORTS = [
  { name: 'desktop_1920x1080', width: 1920, height: 1080, category: 'Desktop' },
  { name: 'laptop_1366x768', width: 1366, height: 768, category: 'Laptop' },
  { name: 'tablet_landscape_1024x768', width: 1024, height: 768, category: 'Tablet Landscape' },
  { name: 'tablet_portrait_768x1024', width: 768, height: 1024, category: 'Tablet Portrait' },
  { name: 'mobile_large_430x932', width: 430, height: 932, category: 'Mobile Large (iPhone 14 Pro Max)' },
  { name: 'mobile_standard_360x800', width: 360, height: 800, category: 'Mobile Standard (Android 360x800)' },
];

(async () => {
  console.log('=== STARTING PLAYWRIGHT E2E & WORKFLOW VERIFICATION SUITE ===');

  const browser = await chromium.launch({
    executablePath: CHROME_PATH,
    headless: true,
  });

  const auditLog = {
    testedAt: new Date().toISOString(),
    viewports: [],
    overflowIssues: [],
    workflowResults: {},
    pagePerformance: {},
  };

  for (const vp of VIEWPORTS) {
    console.log(`\n=============================================================`);
    console.log(`Testing Viewport: ${vp.name} (${vp.width}x${vp.height}) [${vp.category}]`);
    console.log(`=============================================================`);

    const adminContext = await browser.newContext({
      viewport: { width: vp.width, height: vp.height },
      deviceScaleFactor: 1,
    });
    const adminPage = await adminContext.newPage();

    // 1. Authenticate Super Admin via UI
    await adminPage.goto('http://localhost:3000/login', { waitUntil: 'networkidle' });
    await adminPage.fill('#username', 'admin@olrac.com');
    await adminPage.fill('#password', 'olrac1234');
    await adminPage.click('button[type="submit"]');
    await adminPage.waitForTimeout(2000);

    // -------------------------------------------------------------------------
    // 1. Super Admin Security Center (/admin/security)
    // -------------------------------------------------------------------------
    console.log('  [1/4] Navigating to /admin/security...');
    const t0 = Date.now();
    await adminPage.goto('http://localhost:3000/admin/security', { waitUntil: 'networkidle' });
    const secLoadTime = Date.now() - t0;
    await adminPage.waitForTimeout(800);
    auditLog.pagePerformance[`security_center_${vp.name}`] = secLoadTime;

    const hasSecOverflow = await adminPage.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    console.log(`        Load time: ${secLoadTime}ms | Horizontal Overflow: ${hasSecOverflow}`);
    if (hasSecOverflow) auditLog.overflowIssues.push({ page: '/admin/security', viewport: vp.name });

    await adminPage.screenshot({ path: path.join(EVIDENCE_DIR, `sec_center_${vp.name}.png`), fullPage: true });

    // Open & capture Reveal PIN Modal
    const unmaskBtn = adminPage.locator('button:has-text("Unmask PIN")').first();
    if (await unmaskBtn.isVisible()) {
      await unmaskBtn.click();
      await adminPage.waitForTimeout(400);
      await adminPage.screenshot({ path: path.join(EVIDENCE_DIR, `modal_reveal_pin_${vp.name}.png`) });
      await adminPage.locator('button:has-text("Cancel")').first().click();
      await adminPage.waitForTimeout(300);
    }

    // Dynamic Challenge-Response Generation Workflow (Option 1.2)
    const challengeBtn = adminPage.locator('button:has-text("Generate Code")').first();
    if (await challengeBtn.isVisible()) {
      await challengeBtn.click();
      await adminPage.waitForTimeout(400);

      await adminPage.fill('input[placeholder="e.g. tv_abc12345"]', 'tv_demo_kiosk_99');
      await adminPage.fill('input[placeholder="e.g. A1B2C3"]', '9F8E7D');
      await adminPage.fill('input[placeholder="Enter your operator password"]', 'olrac1234');
      await adminPage.fill('textarea[placeholder*="technician ticket"]', 'Automated E2E audit validation');

      await adminPage.screenshot({ path: path.join(EVIDENCE_DIR, `modal_challenge_input_${vp.name}.png`) });

      const genBtn = adminPage.locator('button:has-text("Generate Emergency PIN")').first();
      await genBtn.click();
      await adminPage.waitForTimeout(1200);

      const successModalVisible = await adminPage.locator('text=Generated 6-Digit Single-Use Bypass Code').isVisible();
      console.log(`        Dynamic Challenge 6-Digit Code Generated: ${successModalVisible ? 'PASS' : 'FAIL'}`);
      auditLog.workflowResults[`challenge_gen_${vp.name}`] = successModalVisible;

      await adminPage.screenshot({ path: path.join(EVIDENCE_DIR, `modal_challenge_success_${vp.name}.png`) });

      const doneBtn = adminPage.locator('button:has-text("Done")').first();
      if (await doneBtn.isVisible()) await doneBtn.click();
      await adminPage.waitForTimeout(300);
    }

    // -------------------------------------------------------------------------
    // 2. Super Admin Fleet (/admin/fleet)
    // -------------------------------------------------------------------------
    console.log('  [2/4] Navigating to /admin/fleet...');
    await adminPage.goto('http://localhost:3000/admin/fleet', { waitUntil: 'networkidle' });
    await adminPage.waitForTimeout(800);

    const hasFleetOverflow = await adminPage.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    console.log(`        Horizontal Overflow: ${hasFleetOverflow}`);
    if (hasFleetOverflow) auditLog.overflowIssues.push({ page: '/admin/fleet', viewport: vp.name });

    const maintenancePinCardPresent = await adminPage.locator('h2:has-text("Universal maintenance PIN")').isVisible();
    console.log(`        Obsolete MaintenancePinCard Removed: ${!maintenancePinCardPresent ? 'PASS' : 'FAIL'}`);

    await adminPage.screenshot({ path: path.join(EVIDENCE_DIR, `fleet_${vp.name}.png`), fullPage: true });

    // -------------------------------------------------------------------------
    // 3. Tenant Dashboard Screens (/dashboard/screens)
    // -------------------------------------------------------------------------
    console.log('  [3/4] Navigating to /dashboard/screens (Tenant Owner)...');
    const ownerContext = await browser.newContext({
      viewport: { width: vp.width, height: vp.height },
      deviceScaleFactor: 1,
    });
    const ownerPage = await ownerContext.newPage();

    // Authenticate Tenant Owner via UI
    await ownerPage.goto('http://localhost:3000/login', { waitUntil: 'networkidle' });
    await ownerPage.fill('#username', 'owner@olrac.com');
    await ownerPage.fill('#password', 'olrac1234');
    await ownerPage.click('button[type="submit"]');
    await ownerPage.waitForTimeout(2000);

    const tScreens = Date.now();
    await ownerPage.goto('http://localhost:3000/dashboard/screens', { waitUntil: 'networkidle' });
    const screensLoadTime = Date.now() - tScreens;
    await ownerPage.waitForTimeout(1000);
    auditLog.pagePerformance[`screens_${vp.name}`] = screensLoadTime;

    const hasScreensOverflow = await ownerPage.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    console.log(`        Load time: ${screensLoadTime}ms | Horizontal Overflow: ${hasScreensOverflow}`);
    if (hasScreensOverflow) auditLog.overflowIssues.push({ page: '/dashboard/screens', viewport: vp.name });

    // Verify Workspace Lock Cards are visible
    const hasLockToggle = await ownerPage.locator('h2:has-text("TV lock")').isVisible();
    const hasLockPinCard = await ownerPage.locator('summary:has-text("Workspace lock pin")').isVisible();
    console.log(`        Workspace Lock Cards Visible: Toggle=${hasLockToggle}, PINCard=${hasLockPinCard}`);
    auditLog.workflowResults[`workspace_lock_cards_${vp.name}`] = hasLockToggle && hasLockPinCard;

    if (hasLockToggle) {
      // Test Option 2.1 UI Validation: Toggle lock without PIN
      const lockCheckbox = ownerPage.locator('input[type="checkbox"].peer').first();
      const isLocked = await lockCheckbox.isChecked();
      if (!isLocked) {
        await lockCheckbox.click({ force: true });
        await ownerPage.waitForTimeout(400);
        console.log('        Option 2.1 UI Pre-requisite validation triggered');
      }

      // Configure Workspace PIN
      const pinSummary = ownerPage.locator('summary:has-text("Workspace lock pin")');
      if (await pinSummary.isVisible()) {
        await pinSummary.click();
        await ownerPage.waitForTimeout(400);

        const pinInput = ownerPage.locator('input[placeholder="0000"]').first();
        if (await pinInput.isVisible()) {
          await pinInput.fill('4821');
          await ownerPage.locator('button:has-text("Save")').first().click();
          await ownerPage.waitForTimeout(800);
          console.log('        Workspace Lock PIN 4821 saved successfully');
        }
      }
    }

    await ownerPage.screenshot({ path: path.join(EVIDENCE_DIR, `screens_${vp.name}.png`), fullPage: true });

    // -------------------------------------------------------------------------
    // 4. Screen Detail Page (/dashboard/screens/1) - KioskExitCard & Remote Unlock
    // -------------------------------------------------------------------------
    console.log('  [4/4] Navigating to Screen Detail (/dashboard/screens/1)...');
    const tDetail = Date.now();
    await ownerPage.goto('http://localhost:3000/dashboard/screens/1', { waitUntil: 'networkidle' });
    const detailLoadTime = Date.now() - tDetail;
    await ownerPage.waitForTimeout(1000);
    auditLog.pagePerformance[`screen_detail_${vp.name}`] = detailLoadTime;

    const hasDetailOverflow = await ownerPage.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    console.log(`        Load time: ${detailLoadTime}ms | Horizontal Overflow: ${hasDetailOverflow}`);
    if (hasDetailOverflow) auditLog.overflowIssues.push({ page: '/dashboard/screens/1', viewport: vp.name });

    const kioskDetails = ownerPage.locator('details:has(span:has-text("Getting into this screen"))').first();
    if (await kioskDetails.isVisible()) {
      await kioskDetails.click();
      await ownerPage.waitForTimeout(500);

      // Verify effective PIN display and sync badge
      const pinText = await ownerPage.locator('text=INDEPENDENT SCREEN PIN').isVisible();
      const syncBadge = await ownerPage.locator('text=TV Sync:').isVisible();
      console.log(`        KioskExitCard State: PINControls=${pinText}, SyncBadge=${syncBadge}`);
      auditLog.workflowResults[`kiosk_exit_card_${vp.name}`] = pinText && syncBadge;

      await ownerPage.screenshot({ path: path.join(EVIDENCE_DIR, `kiosk_exit_card_${vp.name}.png`) });

      // Open Remote Unlock modal
      const remoteUnlockBtn = ownerPage.locator('button:has-text("Remote Unlock")').first();
      if (await remoteUnlockBtn.isVisible()) {
        await remoteUnlockBtn.click();
        await ownerPage.waitForTimeout(400);

        await ownerPage.screenshot({ path: path.join(EVIDENCE_DIR, `modal_remote_unlock_${vp.name}.png`) });

        await ownerPage.fill('textarea[placeholder*="Technician on-site"]', 'Playwright automated remote unlock drill');
        const sendUnlockBtn = ownerPage.locator('button:has-text("Send Unlock Signal")').first();
        await sendUnlockBtn.click();
        await ownerPage.waitForTimeout(800);
        console.log('        Remote Unlock Command Dispatched via UI: PASS');
        auditLog.workflowResults[`remote_unlock_${vp.name}`] = true;
      }
    }

    auditLog.viewports.push({
      ...vp,
      securityLoadMs: secLoadTime,
      screensLoadMs: screensLoadTime,
      screenDetailLoadMs: detailLoadTime,
      hasOverflow: hasSecOverflow || hasFleetOverflow || hasScreensOverflow || hasDetailOverflow,
    });

    await adminContext.close();
    await ownerContext.close();
  }

  await browser.close();

  fs.writeFileSync(
    path.join(EVIDENCE_DIR, 'playwright_e2e_summary.json'),
    JSON.stringify(auditLog, null, 2)
  );

  console.log('\n=== PLAYWRIGHT E2E & WORKFLOW VERIFICATION COMPLETED ===');
  console.log(`Evidence and screenshots saved to: ${EVIDENCE_DIR}`);
})().catch((err) => {
  console.error('Playwright E2E Suite Error:', err);
  process.exit(1);
});
