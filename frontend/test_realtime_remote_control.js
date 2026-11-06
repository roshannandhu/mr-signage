const { chromium } = require('playwright');
const { execSync } = require('child_process');

const CHROME_PATH = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';

function adb(cmd) {
  return execSync(`adb -s HNP06KSC ${cmd}`, { encoding: 'utf-8' });
}

function getFocusedWindow() {
  const output = execSync('adb -s HNP06KSC shell dumpsys window windows', { encoding: 'utf-8' });
  const match = output.split('\n').find(l => l.includes('mCurrentFocus') || l.includes('mFocusedApp'));
  return match ? match.trim() : 'Unknown';
}

(async () => {
  console.log('--- STARTING REALTIME REMOTE CONTROL TEST ---');

  // Step 1: Put tablet onto home screen
  console.log('Sending KEYCODE_HOME to tablet...');
  adb('shell input keyevent KEYCODE_HOME');
  await new Promise(r => setTimeout(r, 1000));
  
  let focused = getFocusedWindow();
  console.log('Focused window after HOME:', focused);

  // Step 2: Open browser and go to screen 11 dashboard
  console.log('Launching browser to trigger "Open app on TV"...');
  const browser = await chromium.launch({ executablePath: CHROME_PATH, headless: true });
  const page = await browser.newPage();

  await page.goto('https://app.mrsignage.workers.dev/login');
  await page.fill('#username', 'legitforge@gmail.com');
  await page.fill('#password', 'olrac1234');
  await page.click('button[type="submit"]');
  await page.waitForURL('**/dashboard/**', { timeout: 15000 });

  await page.goto('https://app.mrsignage.workers.dev/dashboard/screens/11');
  await page.waitForTimeout(2000);

  // Clear logcat buffer
  adb('logcat -c');

  // Step 3: Click "Open app on TV"
  console.log('Clicking "Open app on TV"...');
  const startMs = Date.now();
  await page.click('button:has-text("Open app on TV")');

  // Poll for MainActivity to become focused
  let openedInMs = -1;
  for (let i = 0; i < 30; i++) {
    await new Promise(r => setTimeout(r, 200));
    const win = getFocusedWindow();
    if (win.includes('com.olrac.signage.MainActivity')) {
      openedInMs = Date.now() - startMs;
      console.log(`SUCCESS: App brought to front in ${openedInMs}ms! Window: ${win}`);
      break;
    }
  }

  // Get logcat logs
  const logs = adb('logcat -d -s RealtimeClient PlaybackService');
  console.log('Device Logcat:\n', logs);

  await browser.close();
  console.log('--- TEST FINISHED ---');
})().catch(err => {
  console.error('Error during test:', err);
  process.exit(1);
});
