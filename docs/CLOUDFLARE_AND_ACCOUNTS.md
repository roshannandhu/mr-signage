# Cloudflare & Deployment Infrastructure Account Directory

## 1. Primary Cloudflare Production Account (CANONICAL)
- **Account Email**: `mrsignage1100@gmail.com`
- **Cloudflare Account Name**: `Mrsignage1100@gmail.com's Account`
- **Cloudflare Account ID**: `e92ddadc9098dbcf44aadc5653e7a0d3`
- **Subdomain**: `mrsignage.workers.dev`
- **Production Dashboard App URL**: `https://app.mrsignage.workers.dev`
- **Wrangler Project Name**: `app`
- **Configuration File**: `frontend/wrangler.jsonc` (`account_id: "e92ddadc9098dbcf44aadc5653e7a0d3"`)
- **CLI Authentication**: Loaded from `.env.cloudflare` (`CLOUDFLARE_API_TOKEN`).

> [!IMPORTANT]
> All official production frontend deployments MUST target this Cloudflare account (`e92ddadc9098dbcf44aadc5653e7a0d3`).
> `npx wrangler` CLI on the build machine must always be authorized as `mrsignage1100@gmail.com`.

---

## 2. Cloudflare R2 Object Storage Credentials
Used by the FastAPI backend (`backend/.env`):
- **S3 Bucket Name**: `mr-signage-media`
- **S3 Endpoint URL**: `https://e92ddadc9098dbcf44aadc5653e7a0d3.r2.cloudflarestorage.com`
- **Access Key ID**: `74220dbba6eda48ca020c44fdbabb20b`
- **Secret Access Key**: Configured in `backend/.env` as `AWS_SECRET_ACCESS_KEY`
- **Public R2 Base URL**: `https://pub-c710b0b839fa4d3c84211215f176f8b6.r2.dev`

---

## 3. Account Roles & Separation

| Account Email | Role / Purpose | Infrastructure / Scope |
| :--- | :--- | :--- |
| **`mrsignage1100@gmail.com`** | **Production Infrastructure Owner & Super Admin** | Owns Cloudflare account `e92ddadc9098dbcf44aadc5653e7a0d3` (`app.mrsignage.workers.dev`), Super Admin on backend |
| **`roshannandhu1100@gmail.com`** | Secondary developer account | Cloudflare account `a3bb26c552a1e2fd15c08150c195224a` (`app.roshannandhu1100.workers.dev`) |
| **`legitforge@gmail.com`** | **Tenant Client / Workspace Owner** | Tenant User in DB (Organization ID: 110, "Legit Forge's Workspace"). Not a Cloudflare infrastructure owner. |

---

## 4. How to Deploy to Production

Run the following in PowerShell:
```powershell
Get-Content .env.cloudflare | ForEach-Object { if ($_ -match '^([^#=]+)=(.*)$') { [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2]) } }
cd frontend
npm run cf:deploy
```
This builds with OpenNext and deploys directly to `https://app.mrsignage.workers.dev`.
