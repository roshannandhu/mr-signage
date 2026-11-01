import { execFileSync } from 'node:child_process'
import path from 'node:path'

const repoRoot = path.resolve(__dirname, '..', '..')

/** Runs scripts/e2e_seed.py; it refuses any database that is not on this machine. */
export function seed(action: 'create' | 'cleanup') {
  execFileSync(process.env.PYTHON ?? 'python', ['scripts/e2e_seed.py', action], { cwd: repoRoot, stdio: 'inherit' })
}

export const ADMIN = { username: 'e2e-admin', password: process.env.E2E_ADMIN_PASSWORD ?? 'e2e-smoke-pass' }
export const TENANT_NAME = 'E2E Smoke Tenant'
