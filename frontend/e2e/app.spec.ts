import { expect, test, type Page } from '@playwright/test'

const API = process.env.E2E_API_URL ?? 'http://localhost:8000'
const unique = () => `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`

async function register(page: Page, email: string, password: string) {
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Create account' }).click()
  await page.waitForURL('**/dashboard')
}

test('anonymous visitor can shorten a link and it redirects', async ({ page, request }) => {
  await page.goto('/')
  await page.getByLabel('Destination URL').fill('https://example.com/e2e/anonymous')
  await page.getByRole('button', { name: 'Shorten link' }).click()
  const shown = (await page.getByTestId('short-link').textContent()) ?? ''
  const code = shown.slice(shown.lastIndexOf('/') + 1)
  expect(code).toMatch(/^[A-Za-z0-9]{7}$/)
  await expect(page.getByRole('button', { name: 'Copy link' })).toBeVisible()

  const res = await request.get(`${API}/${code}`, { maxRedirects: 0 })
  expect(res.status()).toBe(302)
  expect(res.headers()['location']).toBe('https://example.com/e2e/anonymous')
  expect(res.headers()['cache-control']).toBe('no-store')
})

test('invalid input is explained before any request is made', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('Destination URL').fill('javascript:alert(1)')
  await page.getByRole('button', { name: 'Shorten link' }).click()
  await expect(page.getByRole('alert')).toContainText('http')
})

test('protected pages send anonymous users to sign in', async ({ page }) => {
  await page.goto('/dashboard')
  await expect(page).toHaveURL(/\/login$/)
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
})

test('full journey: register, create, click, see analytics, deactivate, delete', async ({ page, request }) => {
  const id = unique()
  await register(page, `e2e-${id}@example.com`, 'e2e-password-123')
  await expect(page.getByText("You haven't created any links yet")).toBeVisible()

  await page.goto('/links')
  const alias = `e2e-${id}`
  await page.getByLabel('Destination URL').fill('https://example.com/e2e/journey')
  await page.getByRole('button', { name: 'Custom alias and expiry' }).click()
  await page.getByLabel('Custom alias (optional)').fill(alias)
  await page.getByRole('button', { name: 'Shorten link' }).click()
  await expect(page.getByTestId('short-link')).toContainText(alias)

  // Same alias again: a clear conflict message on the field.
  await page.getByLabel('Destination URL').fill('https://example.com/other')
  await page.getByLabel('Custom alias (optional)').fill(alias)
  await page.getByRole('button', { name: 'Shorten link' }).click()
  await expect(page.getByText('already taken')).toBeVisible()

  // Real clicks through the redirect endpoint, with distinguishable metadata.
  for (let i = 0; i < 3; i++) {
    const r = await request.get(`${API}/${alias}`, {
      maxRedirects: 0,
      headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36', Referer: 'https://www.google.com/x' },
    })
    expect(r.status()).toBe(302)
  }

  // The worker aggregates asynchronously; the detail page polls and should reach 3.
  await page.getByRole('link', { name: 'View analytics' }).click()
  await expect(page.getByRole('heading', { name: new RegExp(alias) })).toBeVisible()
  await expect(async () => {
    await page.reload()
    await expect(page.getByText('Total clicks').locator('xpath=following-sibling::dd[1]')).toHaveText('3', { timeout: 2_000 })
  }).toPass({ timeout: 30_000, intervals: [2_000] })
  await expect(page.getByRole('figure', { name: /Browsers: Chrome 100%/ })).toBeVisible()
  await expect(page.getByRole('figure', { name: /Top referrers: google.com 100%/ })).toBeVisible()

  // Deactivating takes effect immediately for visitors.
  await page.getByRole('button', { name: 'Deactivate' }).click()
  await expect(page.getByRole('button', { name: 'Activate' })).toBeVisible()
  expect((await request.get(`${API}/${alias}`, { maxRedirects: 0 })).status()).toBe(410)

  // Delete with confirmation.
  await page.getByRole('button', { name: 'Delete', exact: true }).click()
  await page.getByRole('button', { name: 'Delete link' }).click()
  await page.waitForURL('**/links')
  expect((await request.get(`${API}/${alias}`, { maxRedirects: 0 })).status()).toBe(404)
})

test('users cannot open each other\'s analytics', async ({ browser }) => {
  const id = unique()
  const a = await (await browser.newContext()).newPage()
  await register(a, `owner-${id}@example.com`, 'owner-password-1')
  await a.goto('/links')
  await a.getByLabel('Destination URL').fill('https://example.com/private')
  await a.getByRole('button', { name: 'Shorten link' }).click()
  const shown = (await a.getByTestId('short-link').textContent()) ?? ''
  const code = shown.slice(shown.lastIndexOf('/') + 1)

  const b = await (await browser.newContext()).newPage()
  await register(b, `intruder-${id}@example.com`, 'intruder-password-1')
  await b.goto(`/links/${code}`)
  await expect(b.getByText('does not exist or belongs to another account')).toBeVisible()
})

test('theme choice persists across reloads', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: /Switch to dark theme/ }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
})
