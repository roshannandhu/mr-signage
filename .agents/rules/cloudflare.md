# Cloudflare Account & Deployment Rules

This project's canonical Cloudflare infrastructure is owned exclusively by `mrsignage1100@gmail.com`.

## Production Identity & Configuration
- **Cloudflare Account Name**: `Mrsignage1100@gmail.com's Account`
- **Cloudflare Account ID**: `e92ddadc9098dbcf44aadc5653e7a0d3`
- **Primary Dashboard URL**: `https://app.mrsignage.workers.dev`
- **Wrangler Project Name**: `app`
- **Configuration File**: `frontend/wrangler.jsonc` (must always specify `account_id: "e92ddadc9098dbcf44aadc5653e7a0d3"`)
- **CLI Credentials File**: Stored in local `.env.cloudflare` (gitignored).

## R2 Storage
- **Bucket**: `mr-signage-media`
- **S3 Endpoint**: `https://e92ddadc9098dbcf44aadc5653e7a0d3.r2.cloudflarestorage.com`
- **Access Key ID**: `74220dbba6eda48ca020c44fdbabb20b`
- **Secret Access Key**: stored in `backend/.env` as `AWS_SECRET_ACCESS_KEY`
- **Public URL**: `https://pub-c710b0b839fa4d3c84211215f176f8b6.r2.dev`

## Deployment Command
Always load credentials from `.env.cloudflare` before deploying:
```powershell
Get-Content .env.cloudflare | ForEach-Object { if ($_ -match '^([^#=]+)=(.*)$') { [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2]) } }
cd frontend
npm run cf:deploy
```

## Disambiguation
- Never confuse `legitforge@gmail.com` (which is purely a tenant workspace client) or `roshannandhu1100@gmail.com` with the production Cloudflare owner.
- The production Cloudflare target is ALWAYS `e92ddadc9098dbcf44aadc5653e7a0d3` (`mrsignage1100@gmail.com`).
