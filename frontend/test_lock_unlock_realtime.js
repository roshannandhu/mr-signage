const { chromium } = require('playwright');
const { execSync } = require('child_process');

const CHROME_PATH = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';

function adb(cmd) {
  return execSync(`adb -s HNP06KSC ${cmd}`, { encoding: 'utf-8' });
}

(async () => {
  console.log('--- STARTING REMOTE LOCK / UNLOCK REALTIME TEST ---');

  const browser = await chromium.launch({ executablePath: CHROME_PATH, headless: true });
  const page = await browser.newPage();

  // Login
  await page.goto('https://app.mrsignage.workers.dev/login');
  await page.fill('#username', 'legitforge@gmail.com');
  await page.fill('#password', 'olrac1234');
  await page.click('button[type="submit"]');
  await page.waitForURL('**/dashboard/**', { timeout: 15000 });

  // Navigate to screen 11
  await page.goto('https://app.mrsignage.workers.dev/dashboard/screens/11');
  await page.waitForTimeout(2500);

  // Clear logcat
  adb('logcat -c');

  // Find the lock toggle
  const lockToggle = page.locator('input[type="checkbox"]');
  const isChecked = await lockToggle.isChecked();
  console.log('Current dashboard lock toggle state:', isChecked ? 'LOCKED' : 'UNLOCKED');

  // Test 1: Click toggle to switch state (e.g., if locked -> unlock)
  console.log(`Clicking toggle to switch to ${isChecked ? 'UNLOCKED' : 'LOCKED'}...`);
  const t0 = Date.now();
  await lockToggle.click({ force: true });
  
  // Wait up to 3 seconds for logcat to capture WS event
  let received = false;
  for (let i = 0; i < 15; i++) {
    await new Promise(r => setTimeout(r, 200));
    const logs = adb('logcat -d -s RealtimeClient PlaybackService');
    if (logs.includes('screen_lock_changed') || logs.includes('remote_unlock') || logs.includes('remote_lock')) {
      const elapsed = Date.now() - t0;
      console.log(`REALTIME CONFIRMED! WS message received on tablet in ~${elapsed}ms!`);
      console.log('Relevant Logcat snippet:\n', logs.split('\n').filter(l => l.includes('RealtimeClient') || l.includes('PlaybackService')).slice(-8).join('\n'));
      received = true;
      break;
    }
  }

  if (!received) {
    console.error('FAILED: No WS lock event received in 3000ms!');
  }

  // Test 2: Toggle it back to restore original state
  console.log('\nRestoring lock state back...');
  adb('logcat -c');
  const t1 = Date.now();
  await lockToggle.click({ force: true });
  for (let i = 0; i < 15; i++) {
    await new Promise(r => setTimeout(r, 200));
    const logs = adb('logcat -d -s RealtimeClient PlaybackService');
    if (logs.includes('screen_lock_changed') || logs.includes('remote_unlock') || logs.includes('remote_lock')) {
      const elapsed = Date.now() - t1;
      console.log(`RESTORE CONFIRMED! WS message received on tablet in ~${elapsed}ms!`);
      console.log('Relevant Logcat snippet:\n', logs.split('\n').filter(l => l.includes('RealtimeClient') || l.includes('PlaybackService')).slice(-8).join('\n'));
      break;
    }
  }

  await page.screenshot({ path: 'C:/Users/Roshan Raj/.gemini/antigravity/brain/33beea0f-862c-4744-ad2a-ddb4de7833c9/lock_unlock_test_proof.png' });
  await browser.close();
  console.log('--- TEST COMPLETED ---');
})().catch(err => {
  console.error('Test error:', err);
  process.exit(1);
});
