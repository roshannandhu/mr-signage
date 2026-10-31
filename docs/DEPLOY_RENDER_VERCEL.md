# Deploying to Render + Cloudflare (or Vercel)

The path for a first real-hardware test: API and worker on Render, dashboard on Cloudflare
Workers or Vercel, database on Supabase, media on Cloudflare R2, Redis on Upstash.

`DEPLOYMENT.md` is the all-in-one Docker Compose stack and `DEPLOYMENT_CLOUD.md` is the
Oracle VM split. Follow ONE of the three; they differ in ways that matter.

**Order matters.** Render needs the dashboard's origin for CORS and the dashboard needs
Render's URL for the API, so the sequence is Render, then the dashboard, then back to
Render. Do not try to do it in one pass.

**If the deploying account does not own the repository**, see "Connecting a repository you
do not own" at the end before starting: both Render and Cloudflare connect through a GitHub
App, and only a repository's owner can install one.

---

## 0. Before either dashboard

**a. Generate a signing secret.** Anything guessable makes every issued token forgeable.

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

**b. Supabase.** Create the project, then take the connection string from
*Project Settings -> Database -> Connection string -> Transaction pooler*. It ends in
**:6543**, not 5432. Direct connections exhaust the free tier's limit under a normal fleet.

Put the API in the **same region** as this database. Latency here is round trips times
distance, and a co-located pair is roughly 100x closer than a cross-region one, which is
worth more than any query tuning. Seoul plus Singapore is a bad pair; match them.

**c. Cloudflare R2.** Create a bucket, then *Manage API Tokens -> Create API Token*:

| Setting | Value |
|---|---|
| Permissions | **Object Read & Write** |
| Scope | the bucket you just created |

Note the Access Key ID, the Secret Access Key (shown once), and the S3 endpoint
`https://<account-id>.r2.cloudflarestorage.com`. The bucket stays **private** because the
app hands out presigned URLs with a one-hour expiry, so no public access or custom domain is
needed.

Verify before anything depends on it:

```bash
S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com \
S3_BUCKET_NAME=<your bucket> AWS_REGION=auto \
AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... \
python scripts/check_r2.py
```

**d. Upstash Redis.** Free tier. Copy the `rediss://` URL; TLS is accepted as-is.

Redis is **not optional**. Without it uploads fail outright, nothing is pushed to a screen,
and all six scheduled jobs stop, including the two prunes that keep the database inside
Supabase's 500 MB.

---

## 1. Render: the API and worker

### The short way: the blueprint

`render.yaml` at the repository root encodes every setting below. **New -> Blueprint ->**
pick this repo, and Render prompts only for the eight secrets. Skip to step 2.

The rest of this section is the same thing through the web form, which is worth reading
once because two of its fields fail in ways that do not name themselves.

### The long way: New -> Web Service -> connect the repository.

| Field | Value |
|---|---|
| Language | **Docker** |
| Dockerfile Path | `./backend/Dockerfile` |
| **Docker Build Context Directory** | **`.`** |
| Instance Type | **Starter** or larger |
| Health Check Path | `/api/health` |
| Region | the one holding your Supabase project |

Two of those are not preferences:

- **Docker, not Python.** The media worker shells out to `ffmpeg` and `ffprobe`. Render's
  native Python runtime has neither, so every video upload would fail; the Dockerfile
  installs them.
- **Build context `.`, not `./backend`.** The Dockerfile copies `backend/` as a *package*
  from the repository root. Point the context at `./backend` and the container crash-loops
  on `attempted relative import with no known parent package`.
- **Dockerfile Path must be set**, and it is the field a first deploy usually misses.
  Render defaults to `./Dockerfile`; this repo has none at the root, so the build dies in
  under ten seconds with

        error: failed to solve: failed to read dockerfile:
        open Dockerfile: no such file or directory

  If you see that, this field is the cause -- not the Dockerfile, which is fine.

**Not the free tier.** It sleeps after about 15 minutes idle, which stops the cron jobs, and
the two prunes are what keep `play_logs` inside Supabase's 500 MB. That table grows roughly
316 MB a day at 100 screens, so a sleeping worker fills the database in days. Free is also
smaller than the 1 GB VM that `DEPLOYMENT_CLOUD.md` already warns will be OOM-killed by
libx264.

### Environment

```
DATABASE_URL             postgresql://...@...pooler.supabase.com:6543/postgres
REDIS_URL                rediss://...upstash.io:6379
SECRET_KEY               <from step 0a>
RUN_WORKER_IN_PROCESS    true
WORKER_MAX_JOBS          2
AWS_ACCESS_KEY_ID        <R2 access key id>
AWS_SECRET_ACCESS_KEY    <R2 secret>
AWS_REGION               auto
S3_ENDPOINT_URL          https://<account-id>.r2.cloudflarestorage.com
S3_BUCKET_NAME           <your bucket, exactly>
PLAY_LOG_RETENTION_DAYS  1
ROLLUP_RETENTION_DAYS    400
PAYMENT_PROVIDER         mock
CORS_ORIGINS             http://localhost:3000
PUBLIC_BASE_URL          http://localhost:8000
```

The last two are placeholders, corrected in step 3.

- `RUN_WORKER_IN_PROCESS=true` because one Render service means no separate worker process.
  Without a worker running somewhere, uploads never reach `ready` and never reach a TV, and
  every dashboard count reads zero forever.
- `S3_BUCKET_NAME` must match the bucket exactly; a mismatch surfaces only as a 500 on the
  first upload.
- Setting `AWS_ACCESS_KEY_ID` to anything other than `mock` is the switch that turns R2 on.
  Leave it unset and uploads go to Render's local disk, which is wiped on every deploy.

### Pre-Deploy Command

```
alembic -c backend/alembic.ini upgrade head
```

Migrations run automatically only on a first, empty database. Without this, every later
schema change silently fails to apply.

Deploy, and note the URL: `https://<your-api>.onrender.com`.

---

## 2. The dashboard: Cloudflare Workers, or Vercel

Pick one. Cloudflare is the better fit if the media is already on R2; Vercel is fewer steps.

### 2a. Cloudflare Workers (via OpenNext)

It is a **Worker**, not a Pages project, and that is not a preference. The Pages adapter
(`@cloudflare/next-on-pages`) stops at Next 15.5.2 and cannot build this app; OpenNext is
what Cloudflare maintains for Next now, and it emits a Worker plus a static asset bundle.
Everything Pages gave you -- Git-connected deploys, preview URLs, the free tier -- is on
Workers too. See `frontend/open-next.config.ts` and `frontend/wrangler.jsonc`, both already
committed.

**Workers & Pages -> Create -> Workers -> Import a repository.**

| Field | Value |
|---|---|
| Repository | `mr-signage` |
| **Root directory** | **`frontend`** |
| Build command | `npx opennextjs-cloudflare build` |
| Deploy command | `npx wrangler deploy` |

The Worker name comes from `wrangler.jsonc` (`olrac-signage`), so leave the name field
alone rather than fighting it.

**Build variables** (Settings -> Variables -> *Build* variables, not runtime):

```
NEXT_PUBLIC_API_URL              https://<your-api>.onrender.com
NEXT_PUBLIC_GOOGLE_MAPS_API_KEY  <optional>
```

`NEXT_PUBLIC_*` is inlined by Next at **build** time. Set as a runtime variable it arrives
too late, the bundle keeps its fallback of `http://localhost:8000`, and every request dies
on the visitor's own machine. `wrangler.jsonc` carries a comment saying exactly this.

To deploy by hand instead of from Git:

```bash
cd frontend
NEXT_PUBLIC_API_URL=https://<your-api>.onrender.com npm run cf:deploy
```

Your URL is `https://olrac-signage.<subdomain>.workers.dev`.

Free tier: 100,000 requests/day, and static assets do not count against it. The one limit
worth knowing is **10 ms CPU per invocation**. This dashboard is client-rendered, so almost
every hit is a static asset and never runs the Worker -- but the `[id]` routes do server
render, and if they start returning CPU-limit errors the fix is the $5/month Workers Paid
plan, not a code change.

### 2b. Vercel

**Add New -> Project -> import the repository.**

| Field | Value |
|---|---|
| **Root Directory** | **`frontend`** |
| Framework | Next.js (detected) |

Same two environment variables as above; on Vercel they are ordinary project variables.

There is no `vercel.json` in this repository on purpose. It previously held a
`services`/microfrontends configuration, which is not what this is: one Next app in a
subdirectory, selected with Root Directory.

## 3. Back to Render

Correct the two placeholders and redeploy:

```
CORS_ORIGINS      https://olrac-signage.<subdomain>.workers.dev   (or your Vercel URL)
PUBLIC_BASE_URL   https://<your-api>.onrender.com
```

`PUBLIC_BASE_URL` is stamped into media URLs and provisioning QR codes. Left wrong, TVs
receive links they cannot fetch.

---

## 4. Create the platform operator

`INITIAL_ADMIN_USERNAME`/`PASSWORD` creates an account with role **`owner`**, which is a
tenant account and **cannot reach `/admin`**. Platform status comes only from
`role = 'super_admin'`.

Run locally, pointed at the production database, because `seed_admin` prompts for the
password interactively and Render's shell is a paid add-on:

```bash
DATABASE_URL="<supabase url>" SECRET_KEY="<same secret>" \
  python -m backend.seed_admin admin@yourdomain.com \
  --email admin@yourdomain.com --role super_admin
```

Set `--email` so Google sign-in can match the account by address.

---

## 5. Verify before provisioning a screen

```bash
curl https://<your-api>.onrender.com/api/health
```

Required:

- `"backend": "postgresql"`. **`"sqlite"` means `DATABASE_URL` never loaded and every write
  is discarded on the next deploy. Stop and fix it.**
- `"redis": "connected"`, otherwise uploads fail and no scheduled job runs.

`"status": "degraded"` with the `ALLOW_LEGACY_DEVICE_AUTH` warning is expected until step 6.

Then, in the dashboard: sign in, upload one video, and confirm it reaches `ready`. That
single upload exercises ffmpeg, R2 and Redis together, which is most of what can be
misconfigured.

---

## 6. Before real customers

**Turn off legacy device auth.** `ALLOW_LEGACY_DEVICE_AUTH` defaults to `true`, which lets a
screen call the device endpoints with no credential. A device id is not a secret: the API
echoes it, the dashboard shows it, the logs print it. While this is on, anyone holding one
can read a screen's playlist and its maintenance pin, and can post play logs that bill an
advertiser for spots that never ran.

Set it to `false` once no screen logs `authenticated with no credential (legacy path)`.
`/api/health` reports `degraded` and names this setting while it is on.

**Grant the overlay appop on every panel**, or the dashboard's "Open app" button silently
does nothing on Android 12+:

```bash
adb shell appops set com.olrac.signage SYSTEM_ALERT_WINDOW allow
```

See `DEPLOYMENT.md` for why, and for the rest of the per-panel provisioning.

---

## Known limits of this shape

- **Transcoding is CPU-bound.** Starter is 0.5 CPU; `WORKER_MAX_JOBS=2` with libx264 makes
  the API sluggish while a long video processes. Raise the instance before raising the jobs.
- **Cold starts.** The first request after idle is slow even on paid tiers.
- **Supabase free is 500 MB.** `PLAY_LOG_RETENTION_DAYS=1` above is what keeps 100 screens
  inside it; the rollups preserve the reporting history.
- **Existing local media does not migrate.** Rows already pointing at `/uploads/...` will
  404 after switching to R2 unless the files are copied into the bucket and the rows
  rewritten. Not an issue on a fresh database.

---

## Connecting a repository you do not own

Render and Cloudflare both connect through a **GitHub App**, and on a **personal**
repository only the owner can install one. A collaborator with `write` cannot: GitHub's
`admin` and `maintain` roles exist only on organisation-owned repositories, so the import
screen offers a *request*, not a deploy button.

Two ways through it.

**The owner approves, once per service.** The collaborator starts the import and clicks
through to request access; the owner then approves at
<https://github.com/settings/installations>, granting the app access to this repository
only. Faster still, the owner can install both apps first and skip the waiting:
<https://github.com/apps/render> and the Cloudflare Workers app, each with
*Only select repositories -> mr-signage*.

**Or the collaborator forks it.** A fork is owned by the person who made it, so they can
install apps and deploy with no approval at all, and can keep it current with
`gh repo sync <them>/mr-signage --source <owner>/mr-signage`. This is also the
cheapest way for two people to each hold full control of their own environment: two forks,
two free deployments, nothing shared.

Do **not** move the repository into a GitHub organisation just to solve this. It looks
tidy, but Vercel's free tier refuses organisation-owned repositories, so it converts a
permissions annoyance into a bill. (Cloudflare Workers has no such restriction.)
