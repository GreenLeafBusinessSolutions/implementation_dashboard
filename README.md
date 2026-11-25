# Implementation Pulse

Live status dashboard for payroll implementations built from the **Bundle: New Client Profile Project** template (Standard Layout) in Zoho Projects, plus the matching Zoho CRM deals.

## How it works

```
GitHub Actions (every 20 min, weekdays)          GitHub Pages
  scripts/fetch_data.py  ── Zoho Projects API ─┐
                         ── Zoho CRM COQL ─────┼─> site/data.json ─> site/index.html
  site/tracker.json (Cliq issues, manual) ─────┘
```

- The Zoho credentials stay in GitHub Secrets and are never in the page.
- The open page re-reads `data.json` every 5 minutes and has a **Refresh now** link.
- If the last pull is more than 2 hours old, the page shows a red warning.
- If a Zoho pull fails, nothing is deployed and the last good version stays up. GitHub emails whoever last edited the workflow.

## One-time setup

### 1. Create the Zoho API client
1. Go to https://api-console.zoho.com and choose **Self Client**.
2. Under **Generate Code**, enter these scopes:
   `ZohoProjects.projects.READ,ZohoProjects.portals.READ,ZohoProjects.milestones.READ,ZohoProjects.tasks.READ,ZohoCRM.coql.READ,ZohoCRM.modules.deals.READ,ZohoCRM.settings.fields.READ`
   Set the duration to 10 minutes and click **Create**. Copy the code.
3. Within 10 minutes, swap the code for a refresh token. Run this from any terminal:
   ```
   curl -X POST "https://accounts.zoho.com/oauth/v2/token" \
     -d "grant_type=authorization_code" -d "client_id=CLIENT_ID" \
     -d "client_secret=CLIENT_SECRET" -d "code=THE_CODE"
   ```
   Keep the `refresh_token` from the response. It does not expire unless it is revoked.

Use a Zoho account that can see every implementation project and CRM deal, such as a service or admin account. The dashboard shows only what that account can see.

### 2. Create the repository
1. Create a **private** repo, for example `implementation-pulse`, and push these files.
2. Go to **Settings → Secrets and variables → Actions → New repository secret** and add:
   - `ZOHO_CLIENT_ID`
   - `ZOHO_CLIENT_SECRET`
   - `ZOHO_REFRESH_TOKEN`
3. Go to **Settings → Pages → Build and deployment → Source** and choose **GitHub Actions**.
4. Go to **Actions → Refresh dashboard data → Run workflow**. When the run finishes, the page URL appears in the run summary.

### 3. Lock down who can see it (important)
On GitHub Free and Team plans, a Pages site is **public on the internet even when the repo is private**. That would expose client names and statuses. Options:
- **GitHub Enterprise Cloud:** set Settings → Pages → Visibility to **Private**. Only signed-in org members can open the page.
- **Not on Enterprise:** don't use Pages. Host `site/` behind a login instead, for example AWS S3 + CloudFront with Cognito, or Cloudflare Pages with Cloudflare Access. The fetch script and page work unchanged there.

## Making it closer to real time (optional)
The workflow also accepts a `repository_dispatch` event called `zoho-update`. A Zoho Projects webhook (or a Zoho Flow) can POST to it whenever a project changes:

```
POST https://api.github.com/repos/OWNER/REPO/dispatches
Authorization: Bearer <fine-grained token with Contents: read/write on this repo>
{"event_type":"zoho-update"}
```

Each refresh takes about 1–2 minutes. Scheduled runs use Actions minutes: about 40 runs a day × 22 workdays. That fits within the Team plan's 3,000 minutes, but adjust the cron if you need to.

## Updating the Cliq client-issues table
`site/tracker.json` is not pulled automatically, because its priority and notes come from a review of Cliq chats. To update it, replace the file (same shape, with a new `as_of` line) and commit. The push redeploys the page.

## Settings you may change
| Where | Setting |
|---|---|
| `scripts/fetch_data.py` | `CLOSED`, `CRM_STAGES`, `PIPELINE_EXCLUDE`, layout ID (`ZOHO_LAYOUT_ID`) |
| `site/index.html` | `RISK_DAYS`, `RISK_PCT`, `STALE_DAYS`, `OFF_TEAM` (specialists flagged as off the team), refresh intervals |
| `.github/workflows/refresh.yml` | Schedule |
| Repo variable `ZOHO_DC` | Zoho data center, if not `com` |
