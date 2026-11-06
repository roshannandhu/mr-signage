const { chromium } = require('playwright');
const { execSync } = require('child_process');

const CHROME_PATH = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';

function adb(cmd) {
  return execSync(`adb -s HNP06KSC ${cmd}`, { encoding: 'utf-8' });
}

function captureTablet(filename) {
  adb(`exec-out screencap -p > "C:/Users/Roshan Raj/.gemini/antigravity/brain/33beea0f-862c-4744-ad2a-ddb4de7833c9/${filename}"`);
  console.log(`Captured tablet screenshot: ${filename}`);
}

(async () => {
  console.log('--- STARTING PHASE 1 & 2 LIVE HARDWARE VERIFICATION ---');

  const browser = await chromium.launch({ executablePath: CHROME_PATH, headless: true });
  const page = await browser.newPage();

  // Step 1: Login
  console.log('Logging into dashboard...');
  await page.goto('https://app.mrsignage.workers.dev/login');
  await page.fill('#username', 'legitforge@gmail.com');
  await page.fill('#password', 'olrac1234');
  await page.click('button[type="submit"]');
  await page.waitForURL('**/dashboard/**', { timeout: 15000 });

  // Step 2: Navigate to Screen 11
  await page.goto('https://app.mrsignage.workers.dev/dashboard/screens/11');
  await page.waitForTimeout(3000);

  const lockToggle = page.locator('input[type="checkbox"]');
  let isLocked = await lockToggle.isChecked();
  console.log('Initial lock state on dashboard:', isLocked ? 'LOCKED' : 'UNLOCKED');

  // If already unlocked, lock it first so we can test unlock transition cleanly
  if (!isLocked) {
    console.log('Locking first to set baseline...');
    await lockToggle.click({ force: true });
    await page.waitForTimeout(3000);
  }

  // Clear logcat
  adb('logcat -c');

  // Step 3: Trigger REMOTE UNLOCK
  console.log('Triggering REMOTE UNLOCK from dashboard...');
  await lockToggle.click({ force: true });
  
  // Wait ~600ms for WebSocket delivery and animation to show pill
  await page.waitForTimeout(700);

  // Capture tablet screen during pill display
  captureTablet('proof_remote_unlock_pill.png');

  // Verify logcat on tablet
  const unlockLogs = adb('logcat -d -s PlaybackService RealtimeClient');
  console.log('Tablet logcat after remote unlock:\n', unlockLogs);

  // Wait 4 seconds for pill to auto-dismiss and floating gear to remain
  console.log('Waiting for pill to auto-dismiss (3.5s)...');
  await page.waitForTimeout(4000);
  captureTablet('proof_unlocked_with_gear.png');

  // Step 4: Trigger REMOTE LOCK
  console.log('Triggering REMOTE LOCK from dashboard...');
  adb('logcat -c');
  await lockToggle.click({ force: true });
  await page.waitForTimeout(700);

  // Capture tablet screen during lock pill display
  captureTablet('proof_remote_lock_pill.png');

  const lockLogs = adb('logcat -d -s PlaybackService RealtimeClient');
  console.log('Tablet logcat after remote lock:\n', lockLogs);

  // Wait 4 seconds for lock pill to auto-dismiss
  await page.waitForTimeout(4000);
  captureTablet('proof_locked_normal_playback.png');

  await browser.close();
  console.log('--- PHASE 1 & 2 TEST COMPLETED SUCCESSFULLY ---');
})().catch(err => {
  console.error('Test error:', err);
  process.exit(1);
});
