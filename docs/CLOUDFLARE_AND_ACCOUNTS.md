# Cloudflare & Deployment Infrastructure Account Directory

## 1. Primary Cloudflare Production Account (CANONICAL)
- **Account Email**: `mrsignage1100@gmail.com`
- **Cloudflare Account ID**: `e92ddadc9098dbcf44aadc5653e7a0d3`
- **Subdomain**: `mrsignage.workers.dev`
- **Production Dashboard App URL**: `https://app.mrsignage.workers.dev`
- **Wrangler Project Name**: `app`
- **Configuration File**: `frontend/wrangler.jsonc` (`account_id: "e92ddadc9098dbcf44aadc5653e7a0d3"`)

> [!IMPORTANT]
> All official production frontend deployments MUST target this Cloudflare account (`e92ddadc9098dbcf44aadc5653e7a0d3`).
> `npx wrangler` CLI on the build machine must always be authorized as `mrsignage1100@gmail.com`.

---

## 2. Distinction Between Accounts

| Account Email | Role / Purpose | Infrastructure / Scope |
| :--- | :--- | :--- |
| **`mrsignage1100@gmail.com`** | **Production Infrastructure Owner & Super Admin** | Owns Cloudflare account `e92ddadc9098dbcf44aadc5653e7a0d3` (`app.mrsignage.workers.dev`), Super Admin on backend |
| **`roshannandhu1100@gmail.com`** | Secondary developer account | Cloudflare account `a3bb26c552a1e2fd15c08150c195224a` (`app.roshannandhu1100.workers.dev`) |
| **`legitforge@gmail.com`** | **Tenant Client / Workspace Owner** | Tenant User in DB (Organization ID: 110, "Legit Forge's Workspace"). Not a Cloudflare infrastructure owner. |

---

## 3. How to Deploy to Production

1. Ensure `frontend/wrangler.jsonc` has:
   ```jsonc
   {
     "name": "app",
     "account_id": "e92ddadc9098dbcf44aadc5653e7a0d3",
     "main": ".open-next/worker.js"
   }
   ```
2. When logging in via Wrangler CLI:
   ```bash
   cd frontend
   npx wrangler login
   ```
   Select / authorize **`mrsignage1100@gmail.com`** in the browser.
3. Deploy:
   ```bash
   npm run cf:deploy
   ```
   This deploys directly to `https://app.mrsignage.workers.dev`.
