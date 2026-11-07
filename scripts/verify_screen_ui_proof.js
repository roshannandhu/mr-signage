const { chromium } = require('../frontend/node_modules/@playwright/test');
const path = require('path');
const fs = require('fs');

const CHROME_PATH = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const ARTIFACTS_DIR = 'C:/Users/Roshan Raj/.gemini/antigravity/brain/15d6cc16-8d55-4fc5-9a9b-010375c08891';

(async () => {
  console.log('=== VERIFYING SCREEN DETAIL & PLAYBACK TIMELINE UI (DESKTOP & MOBILE) ===');
  const browser = await chromium.launch({ executablePath: CHROME_PATH, headless: true });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 850 },
    deviceScaleFactor: 1.5,
  });
  const page = await context.newPage();

  console.log('1. Logging in as legitforge@gmail.com...');
  await page.goto('http://localhost:3000/login', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForSelector('#username', { timeout: 10000 });
  await page.fill('#username', 'legitforge@gmail.com');
  await page.fill('#password', 'olrac1234');
  await page.click('button[type="submit"]');
  await page.waitForTimeout(2000);

  // 1. DESKTOP VIEWPORT
  console.log('\n2. Navigating to /dashboard/screens/11 on Desktop (1280x850)...');
  await page.goto('http://localhost:3000/dashboard/screens/11', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(2500);

  const desktopHeaderScreenshot = path.join(ARTIFACTS_DIR, 'desktop_redesigned_proof.png');
  await page.screenshot({ path: desktopHeaderScreenshot, fullPage: false });
  console.log(`[OK] Desktop Header screenshot saved: ${desktopHeaderScreenshot}`);

  // Scroll to timeline on desktop
  const timelineHeading = page.locator('#timeline-title');
  if (await timelineHeading.isVisible()) {
    await timelineHeading.scrollIntoViewIfNeeded();
    await page.waitForTimeout(1000);
    const desktopTimelineScreenshot = path.join(ARTIFACTS_DIR, 'desktop_timeline_proof.png');
    await page.screenshot({ path: desktopTimelineScreenshot, fullPage: false });
    console.log(`[OK] Desktop Timeline screenshot saved: ${desktopTimelineScreenshot}`);
  }

  // 2. MOBILE VIEWPORT (390x844 - iPhone / Smartphone)
  console.log('\n3. Resizing viewport to Mobile (390x844)...');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(1500);

  const mobileHeaderScreenshot = path.join(ARTIFACTS_DIR, 'mobile_redesigned_proof.png');
  await page.screenshot({ path: mobileHeaderScreenshot, fullPage: false });
  console.log(`[OK] Mobile Header screenshot saved: ${mobileHeaderScreenshot}`);

  // Scroll to mobile timeline
  if (await timelineHeading.isVisible()) {
    await timelineHeading.scrollIntoViewIfNeeded();
    await page.waitForTimeout(1000);
    const mobileTimelineScreenshot = path.join(ARTIFACTS_DIR, 'mobile_timeline_proof.png');
    await page.screenshot({ path: mobileTimelineScreenshot, fullPage: false });
    console.log(`[OK] Mobile Timeline screenshot saved: ${mobileTimelineScreenshot}`);
  }

  // 3. MOBILE BOTTOM SHEET INTERACTION
  console.log('\n4. Testing Mobile Bottom Sheet Drawer...');
  const addBtn = page.locator('button:has-text("Add from library")').first();
  const isAddVisible = await addBtn.isVisible();
  console.log('- "Add from library" button visible on mobile:', isAddVisible);

  if (isAddVisible) {
    console.log('Opening mobile bottom sheet...');
    await addBtn.click();
    await page.waitForTimeout(1500);

    const sheetScreenshot = path.join(ARTIFACTS_DIR, 'mobile_sheet_proof.png');
    await page.screenshot({ path: sheetScreenshot, fullPage: false });
    console.log(`[OK] Mobile sheet screenshot saved: ${sheetScreenshot}`);
  }

  await browser.close();
  console.log('\n=== ALL UI VERIFICATIONS COMPLETE ===');
})().catch(err => {
  console.error('Test error:', err);
  process.exit(1);
});
