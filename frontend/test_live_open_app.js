const { chromium } = require('playwright');

const CHROME_PATH = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';

(async () => {
  console.log('Testing live Open app on TV button on app.mrsignage.workers.dev...');
  const browser = await chromium.launch({ executablePath: CHROME_PATH, headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();

  // Login
  await page.goto('https://app.mrsignage.workers.dev/login');
  await page.fill('#username', 'legitforge@gmail.com');
  await page.fill('#password', 'olrac1234');
  await page.click('button[type="submit"]');
  await page.waitForURL('**/dashboard/**', { timeout: 15000 });

  // Navigate to screen 11
  await page.goto('https://app.mrsignage.workers.dev/dashboard/screens/11');
  await page.waitForTimeout(2500);

  // Click Open app on TV
  const btn = page.locator('button:has-text("Open app on TV")');
  await btn.click();
  await page.waitForTimeout(1500);

  // Check toast
  const successToast = await page.locator('text=Command sent: opening signage app on screen.').isVisible();
  console.log('Success toast visible:', successToast);

  await page.screenshot({ path: 'C:/Users/Roshan Raj/.gemini/antigravity/brain/33beea0f-862c-4744-ad2a-ddb4de7833c9/open_app_on_tv_proof.png' });
  await browser.close();
  console.log('Test complete!');
})().catch(err => {
  console.error('Error:', err);
  process.exit(1);
});
