# MR Signage

MR Signage is an end-to-end digital signage platform for centrally managing Android TV displays. It combines a FastAPI control plane, a polished Next.js operations portal, and an offline-ready Kotlin player.

## What it supports

- Media library with local or Cloudflare R2/S3 storage, tags, image previews, and best-effort video thumbnails through `ffmpeg`.
- Drag-and-drop playlists with per-item duration, start/end dates, weekday selection, and daily time windows.
- Screen pairing, heartbeat status, device/storage reporting, direct assignment, and screen groups with one-action publishing.
- Cheap player polling through playlist version markers and `204 Not Modified` responses.
- Foreground 60-second sync (server configurable), immediate reconnect sync, and atomic playlist activation after every media file is cached.
- Android Room/file caching with local-clock schedule filtering, including overnight windows.
- Real user accounts with JWT authentication and `owner`, `editor`, and `viewer` permissions. There is no `admin/admin` bypass.
- Staged player rollout. A published build starts as a **draft** and reaches nobody; pin it
  to a few screens to form a canary ring, then promote it to **released** for every screen
  that has no pin. A screen that fails to install its pinned build three times in a row is
  unpinned automatically and stays on the version it is already running.
- Player releases carry a mandatory SHA-256 digest and an `https` APK URL. The player
  refuses to install a build it cannot verify. Publishing and promoting are restricted to
  the `super_admin` platform account, because a release reaches every tenant's fleet;
  organisation owners can still pin their own screens. Silent installation requires
  device-owner provisioning (see P9).

## Project structure

```text
android-tv/   Kotlin / Jetpack Compose TV player
backend/      FastAPI, SQLAlchemy, and Alembic
frontend/     Next.js 16 operations portal
tests/        Isolated backend checks and the optional live E2E flow
uploads/      Local development media storage
```

## Local setup

### 1. Configure the backend

Copy `backend/.env.example` to `backend/.env` and set a long random `SECRET_KEY`. To bootstrap the first real owner on an empty database, set `INITIAL_ADMIN_USERNAME` and `INITIAL_ADMIN_PASSWORD` for the first startup, or run the interactive seed command:

```bash
python -m backend.seed_admin owner
```

Publishing player releases needs the platform operator account, which has no signup or
dashboard path on purpose — it is the only role that can push an APK to every tenant:

```bash
python -m backend.seed_admin platform-ops --role super_admin
```

Install runtime and test dependencies:

```bash
pip install -r backend/requirements-dev.txt
```

### 2. Apply database migrations

From the repository root:

```bash
alembic -c backend/alembic.ini upgrade head
```

`backend/venv/` in this repository is a Windows environment (`Scripts/`, `.exe`). On
macOS or Linux create your own:

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements-dev.txt
```

### 3. Run the applications

```bash
# Terminal 1, repository root
uvicorn backend.main:app --reload --port 8000

# Terminal 2
cd frontend
npm install
npm run dev
```

The portal is available at `http://localhost:3000` and the API documentation at `http://localhost:8000/docs`.

## Docker Compose

Set at minimum `SECRET_KEY`, `POSTGRES_PASSWORD`, and either initial owner credentials or a pre-seeded database in the root `.env`, then run:

```bash
docker compose up -d --build
docker compose exec backend alembic upgrade head
```

For R2/S3, also set `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `S3_ENDPOINT_URL`, and `S3_BUCKET_NAME`.

## Android TV

The player supports Android TV 8.0+ (`minSdk 26`). The server URL is selected at
build time and can also be changed on the TV with the remote's Menu/Settings key.
Development builds permit HTTP for LAN testing; production builds require HTTPS.

```bash
cd android-tv

# Emulator/LAN development build
gradle :app:assembleDevDebug -PMR_API_BASE_URL=http://192.168.1.20:8000/

# Production-flavor build; use the real public HTTPS API URL
gradle :app:assembleProductionRelease -PMR_API_BASE_URL=https://signage.example.com/
```

On first launch, the player displays a six-digit pairing code. After pairing, it
caches assigned media and evaluates schedules locally so playback continues without
a connection.

For unattended recovery after a power cut, provision MR Signage as the TV's default HOME
launcher. The setup screen can open Android's launcher-role chooser. Some OEM TV
launchers ignore the standard HOME role; those devices must be enrolled with the
vendor's device-owner/kiosk tooling and have MR Signage selected as the persistent HOME
app. A foreground boot service is also installed, but modern Android does not
guarantee that a non-HOME app may bring an activity to the foreground from a boot
broadcast.

See [android-tv/README.md](android-tv/README.md) for installation, kiosk provisioning,
server configuration, offline verification, and update-recovery checks.

## Verification

Run from the repository root:

```bash
python -m pytest tests -q
python tests/validation.py

cd frontend
npx tsc --noEmit
npm run lint
npm run build

cd ../android-tv
./gradlew :app:test
./gradlew :app:assembleDebug
```

The optional live HTTP test requires a running backend and explicit account credentials:

```bash
set MR_TEST_USERNAME=owner
set MR_TEST_PASSWORD=your-password
python tests/e2e_test.py
```

See `tests/README.md` for details.
