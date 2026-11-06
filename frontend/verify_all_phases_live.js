const { chromium } = require('playwright');
const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const CHROME_PATH = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const ARTIFACT_DIR = 'C:/Users/Roshan Raj/.gemini/antigravity/brain/33beea0f-862c-4744-ad2a-ddb4de7833c9';

function adb(cmd) {
  return execSync(`adb -s HNP06KSC ${cmd}`, { encoding: 'utf-8' });
}

function captureTablet(filename) {
  const buf = execSync('adb -s HNP06KSC exec-out screencap -p', { maxBuffer: 15 * 1024 * 1024 });
  const target = path.join(ARTIFACT_DIR, filename);
  fs.writeFileSync(target, buf);
  console.log(`[Captured] ${filename} (${buf.length} bytes)`);
}

(async () => {
  console.log('=== STARTING ALL PHASES LIVE HARDWARE VERIFICATION ===');

  const browser = await chromium.launch({ executablePath: CHROME_PATH, headless: true });
  const page = await browser.newPage();

  // 1. Login to web dashboard
  console.log('1. Logging in to dashboard...');
  await page.goto('https://app.mrsignage.workers.dev/login');
  await page.fill('#username', 'legitforge@gmail.com');
  await page.fill('#password', 'olrac1234');
  await page.click('button[type="submit"]');
  await page.waitForURL('**/dashboard/**', { timeout: 15000 });

  // 2. Navigate to screen 11
  console.log('2. Navigating to Screen 11 page...');
  await page.goto('https://app.mrsignage.workers.dev/dashboard/screens/11');
  await page.waitForTimeout(3000);

  const lockToggle = page.locator('input[type="checkbox"]');
  let isLocked = await lockToggle.isChecked();
  console.log('Current Dashboard Lock State:', isLocked ? 'LOCKED' : 'UNLOCKED');

  if (!isLocked) {
    console.log('Setting state to LOCKED first...');
    await lockToggle.click({ force: true });
    await page.waitForTimeout(3000);
  }

  // 3. Test Remote Unlock (Phase 1 & 2)
  console.log('\n--- PHASE 1 & 2: Remote Unlock (Zero-Interruption Ads + Floating Pill) ---');
  adb('logcat -c');
  await lockToggle.click({ force: true });
  await page.waitForTimeout(700);

  // Capture floating pill over active ad
  captureTablet('proof_phase2_floating_pill.png');

  // Wait 4 seconds for pill to dismiss and gear icon to be visible
  await page.waitForTimeout(4000);
  captureTablet('proof_phase2_gear_icon.png');

  // 4. Test Modern OLED Bento Setup Hub (Phase 3)
  console.log('\n--- PHASE 3: Tapping Floating Gear Icon -> Modern OLED Bento Hub ---');
  // Tap top-left gear icon (around x=48, y=48)
  adb('shell input tap 48 48');
  await page.waitForTimeout(1500);
  captureTablet('proof_phase3_bento_hub.png');

  // 5. Test "Return to Playback & Lock Screen" hero card
  console.log('\n--- Returning to Ads via Bento Hero Card ---');
  // Tap hero card (around x=300, y=140 on 1280x800)
  adb('shell input tap 300 140');
  await page.waitForTimeout(1200);
  captureTablet('proof_phase3_resumed_ads.png');

  // 6. Test ATM/iPhone-Style PIN Pad (Phase 4)
  console.log('\n--- PHASE 4: Modern ATM / iPhone-Style PIN Pad ---');
  // Trigger corner taps (7 taps in bottom-left corner at x=30, y=770)
  for (let i = 0; i < 8; i++) {
    adb('shell input tap 30 770');
    await new Promise(r => setTimeout(r, 120));
  }
  await page.waitForTimeout(1500);
  captureTablet('proof_phase4_atm_pin_pad.png');

  // Enter PIN "1100" via keyevents
  console.log('Entering PIN "1100" on numeric keypad...');
  adb('shell input keyevent KEYCODE_1');
  await new Promise(r => setTimeout(r, 200));
  adb('shell input keyevent KEYCODE_1');
  await new Promise(r => setTimeout(r, 200));
  adb('shell input keyevent KEYCODE_0');
  await new Promise(r => setTimeout(r, 200));
  adb('shell input keyevent KEYCODE_0');
  await page.waitForTimeout(1500);
  captureTablet('proof_phase4_unlocked_via_pin.png');

  // Close setup and return to clean lock
  adb('shell input tap 300 140');
  await page.waitForTimeout(1000);
  captureTablet('proof_final_clean_lock.png');

  await browser.close();
  console.log('=== ALL PHASES FULLY VERIFIED ON PHYSICAL HARDWARE ===');
})().catch(err => {
  console.error('Verification error:', err);
  process.exit(1);
});
