const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const CHROME_PATH = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const ARTIFACTS_DIR = 'C:/Users/Roshan Raj/.gemini/antigravity/brain/33beea0f-862c-4744-ad2a-ddb4de7833c9';

(async () => {
  console.log('=== VERIFYING SINGLE CLEAN LOCK SWITCH ON MOBILE ===');
  const browser = await chromium.launch({ executablePath: CHROME_PATH, headless: true });
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
  });
  const page = await context.newPage();

  // 1. Login
  await page.goto('http://localhost:3000/login', { waitUntil: 'networkidle' });
  await page.fill('#username', 'legitforge@gmail.com');
  await page.fill('#password', 'olrac1234');
  await page.click('button[type="submit"]');
  await page.waitForURL('**/dashboard/**', { timeout: 15000 });

  // 2. Go to Screen 11 detail page
  await page.goto('http://localhost:3000/dashboard/screens/11', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2500);

  // Capture top screen lock proof
  const screenshotPath = path.join(ARTIFACTS_DIR, 'mobile_lock_clean_proof.png');
  await page.screenshot({ path: screenshotPath, fullPage: false });
  console.log(`Saved screenshot: ${screenshotPath}`);

  // Count checkboxes on the entire page
  const checkboxCount = await page.locator('input[type="checkbox"]').count();
  console.log(`Total toggle checkboxes on page: ${checkboxCount}`);

  await browser.close();
  console.log('=== VERIFICATION COMPLETE ===');
})().catch(err => {
  console.error('Test error:', err);
  process.exit(1);
});
