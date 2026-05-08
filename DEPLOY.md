# Deployment guide

This repository contains two parts:

1. **Static viewer** on the `gh-pages` branch — served at https://abhiiibabariya-dev.github.io/cyberjobs-dashboard/
2. **Backend Flask app** on the `master` branch — *needs a real server* to run (login, CV upload, scheduled scanning across all portals, multi-user accounts)

The static viewer alone has no login, no CV upload, no automatic refresh. To get all features, you must deploy the Flask app from `master` to one of the providers below.

---

## Option A — Fly.io (recommended)

**Time:** ~10 minutes (+ install flyctl). **Cost:** free tier with 1 GB persistent volume. **Best balance of cost and durability** — uploaded CVs and user accounts survive container restarts.

1. Install flyctl: https://fly.io/docs/hands-on/install-flyctl/
2. `fly auth login` (opens browser, signs in or signs up).
3. From the repo root: `fly launch --copy-config --no-deploy`
   - When asked, accept the existing `fly.toml`.
   - Pick a region (`bom` = Mumbai is preset; change if you prefer).
4. `fly volumes create cyberjobs_data --region bom --size 1`
5. `fly secrets set ADMIN_SECRET=$(openssl rand -hex 24)`
6. `fly secrets set BREVO_API_KEY=…` *(optional, for email alerts)*
7. `fly deploy`

Your URL will be `https://cyberjobs-dashboard.fly.dev`. User accounts and uploaded CVs persist across restarts because the `/data` volume is mounted.

---

## Option B — Railway

**Time:** ~5 minutes. **Cost:** free trial; ~$5/month after.

1. Visit https://railway.app → New Project → Deploy from GitHub.
2. Pick this repo. Railway detects the `Procfile` and `Dockerfile` automatically.
3. Set environment variables in the **Variables** tab:
   - `ADMIN_SECRET` — any long random string
   - `BREVO_API_KEY` — optional
   - `SOC_STRICT=true`
   - `REQUIRE_RESUME=true`
4. Add a 1 GB volume mounted at `/data`, set `DATA_DIR=/data`.
5. Deploy.

---

## Option C — Self-hosted Docker

**Time:** ~5 minutes if you already have Docker on a VPS.

```bash
docker build -t cyberjobs .
docker run -d \
  --name cyberjobs \
  --restart unless-stopped \
  -p 8080:8080 \
  -v cyberjobs_data:/data \
  -e ADMIN_SECRET="$(openssl rand -hex 24)" \
  -e BREVO_API_KEY=your-key-here \
  -e SOC_STRICT=true \
  -e REQUIRE_RESUME=true \
  cyberjobs
```

Behind nginx / Caddy / Traefik for HTTPS. Works on any cloud or home VPS.

---

## Connecting the static viewer to your live app

Once deployed, open the static viewer at https://abhiiibabariya-dev.github.io/cyberjobs-dashboard/ and paste your deployment URL into the deploy banner's "After deploy, paste your URL" field, then click **Connect**. The page will reload pulling live data from `<your-url>/api/jobs`.

This is stored in your browser's localStorage under `cyberjobs:v1:apiBase`, so subsequent visits remember it. To clear: open DevTools → Application → Local Storage → delete the key.

For a permanent connection (so visitors see the live data without pasting), edit `assets/app.js` on the `gh-pages` branch:

```js
// Near the top of the IIFE.
const DEFAULT_API_BASE = 'https://your-deployment-url.example.com';
```

---

## Required environment variables

| Variable | Purpose | Required |
|---|---|---|
| `ADMIN_SECRET` | Admin panel password | Yes (or admin disabled) |
| `BREVO_API_KEY` | Brevo (Sendinblue) API key for email alerts | No |
| `SOC_STRICT` | `true` filters jobs to cybersecurity only | Default `true` |
| `REQUIRE_RESUME` | `true` blocks login until CV uploaded | Default `true` |
| `SCAN_INTERVAL_MINUTES` | Minutes between scheduled scans (min 5) | Default `30` |
| `DATA_DIR` | Path for persistent data files | Optional (Fly.io: `/data`) |
| `PORT` | Listen port (set automatically by host) | Default `8080` |

## What the backend gives you that the static viewer can't

- Login + create account flow with bcrypt password hashing
- **Mandatory CV upload** (PDF parsed for skills with PyPDF2)
- Live job scanning every `SCAN_INTERVAL_MINUTES` across LinkedIn, TimesJobs, Glassdoor, Foundit, SimplyHired, plus Selenium-based Naukri/Indeed/Google Jobs/LinkedIn Feed when run on a host with Chrome
- Email alerts when matching roles appear (Brevo integration)
- Per-user bookmarks, application notes, salary insights, recruiter contact log
- Admin panel for data sync, user management, and one-click manual scans

## Why "every minute" scanning isn't a good idea

Even though `SCAN_INTERVAL_MINUTES` accepts low values, hammering LinkedIn's guest API every 60 seconds will get the host's IP rate-limited within hours. The default of 30 minutes is the sustainable sweet spot. The minimum we enforce is 5 minutes.
