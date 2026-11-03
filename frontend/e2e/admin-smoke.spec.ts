import { expect, test, type Page } from '@playwright/test'
import { ADMIN, TENANT_NAME } from './seed'

const ADMIN_PAGES = [
  { path: '/admin', crumb: 'Mission Control' },
  { path: '/admin/approvals', crumb: 'Review Center' },
  { path: '/admin/support', crumb: 'Attention Queue' },
  { path: '/admin/tenants', crumb: 'Tenants' },
  { path: '/admin/packages', crumb: 'Packages' },
  { path: '/admin/billing', crumb: 'Billing' },
  { path: '/admin/fleet', crumb: 'TV Fleet' },
  { path: '/admin/releases', crumb: 'App Releases' },
  { path: '/admin/provisioning', crumb: 'Provisioning' },
  { path: '/admin/security', crumb: 'Security Center' },
  { path: '/admin/audit', crumb: 'Audit Log' },
  { path: '/admin/health', crumb: 'Platform Health' },
  { path: '/admin/settings', crumb: 'System Settings' },
  { path: '/admin/storage', crumb: 'Storage' },
  { path: '/admin/demo-video', crumb: 'Demo Video' },
]

async function signIn(page: Page) {
  await page.goto('/login')
  await page.getByPlaceholder('you@company.com').fill(ADMIN.username)
  await page.locator('input[type="password"]').fill(ADMIN.password)
  await page.locator('button[type="submit"]').click()
  await page.waitForURL('**/admin')
}

/** Uncaught exceptions and console errors, minus failed resource loads (a 404 thumbnail is not a crash). */
function collectErrors(page: Page) {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`))
  page.on('console', (message) => {
    if (message.type() === 'error' && !message.text().startsWith('Failed to load resource')) {
      errors.push(`console: ${message.text()}`)
    }
  })
  return errors
}

const currentCrumb = (page: Page) => page.locator('nav[aria-label="Breadcrumb"] [aria-current="page"]')

test.beforeEach(async ({ page }) => {
  // Ensure all API calls route strictly to the isolated test backend on port 8011
  await page.route('**/api/**', async (route) => {
    try {
      const req = route.request()
      const url = new URL(req.url())
      const targetUrl = `http://127.0.0.1:8011${url.pathname}${url.search}`
      const response = await page.request.fetch(targetUrl, {
        method: req.method(),
        headers: req.headers(),
        data: req.postDataBuffer() || undefined,
      })
      await route.fulfill({ response })
    } catch {
      await route.abort('failed').catch(() => {})
    }
  })
})

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('every admin page loads with its breadcrumb and no errors', async ({ page }) => {
  const errors = collectErrors(page)
  await signIn(page)
  for (const { path, crumb } of ADMIN_PAGES) {
    await page.goto(path)
    await expect(currentCrumb(page)).toHaveText(crumb)
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  }
  expect(errors).toEqual([])
})

test('background is pure black in dark mode and pure white in light mode', async ({ page }) => {
  await signIn(page)
  const background = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor)

  await page.emulateMedia({ colorScheme: 'dark' })
  await page.reload()
  await expect.poll(background).toBe('rgb(0, 0, 0)')

  await page.getByRole('button', { name: 'Switch to light mode' }).click()
  await expect.poll(background).toBe('rgb(255, 255, 255)')
  await page.reload()
  await expect.poll(background).toBe('rgb(255, 255, 255)')
})

test('removing a workspace needs a reason and its name typed', async ({ page }) => {
  await signIn(page)
  await page.goto('/admin/tenants')
  const actionBtn = page.getByRole('button', { name: `Actions for ${TENANT_NAME}`, exact: true })
  await actionBtn.click()
  await page.getByRole('menuitem', { name: 'Remove workspace' }).click()

  const dialog = page.getByRole('dialog')
  const submit = dialog.getByRole('button', { name: 'Remove workspace' })
  await expect(submit).toBeDisabled()
  await dialog.getByLabel('Reason (saved to the audit log)').fill('e2e smoke check')
  await dialog.getByLabel(/to confirm/).fill('E2E Smoke')
  await expect(submit).toBeDisabled()
  await dialog.getByLabel(/to confirm/).fill(TENANT_NAME)
  await expect(submit).toBeEnabled()

  // Cancel: the check is that the guard holds, not that the tenant is removed.
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(dialog).toBeHidden()
  await expect(actionBtn).toBeVisible()
})

test('phone navigation opens as a drawer and closes on navigation', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await signIn(page)
  const nav = page.getByRole('complementary', { name: 'Admin navigation' })
  await expect(nav.getByRole('link', { name: 'Tenants' })).not.toBeInViewport()

  await page.getByRole('button', { name: 'Open admin navigation' }).click()
  await expect(nav.getByRole('link', { name: 'Tenants' })).toBeInViewport()
  await nav.getByRole('link', { name: 'Tenants' }).click()
  await page.waitForURL('**/admin/tenants')
  await expect(nav.getByRole('link', { name: 'Tenants' })).not.toBeInViewport()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375)
})
