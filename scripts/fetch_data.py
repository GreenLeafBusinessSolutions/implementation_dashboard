#!/usr/bin/env python3
"""Pull Implementation Pulse data from Zoho Projects and Zoho CRM and write site/data.json.

Runs in GitHub Actions. Needs these environment variables (stored as GitHub Secrets):
  ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, ZOHO_REFRESH_TOKEN
Optional:
  ZOHO_DC            accounts/API domain suffix, default "com" (use "eu", "in", "com.au" etc. if needed)
  ZOHO_PORTAL_ID     default 681677986 (greenleafbizsolutions)
  ZOHO_LAYOUT_ID     default 1384638000000020008 (Standard Layout = Bundle: New Client Profile Project)

Standard library only, so the workflow needs no pip install.
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DC = os.environ.get("ZOHO_DC", "com")
PORTAL = os.environ.get("ZOHO_PORTAL_ID", "681677986")
LAYOUT = os.environ.get("ZOHO_LAYOUT_ID", "1384638000000020008")
ACCOUNTS = f"https://accounts.zoho.{DC}"
PROJECTS_API = f"https://projectsapi.zoho.{DC}/api/v3/portal/{PORTAL}"
CRM_API = f"https://www.zohoapis.{DC}/crm/v7"
OUT = os.path.join(os.path.dirname(__file__), "..", "site", "data.json")

# Statuses the page treats as closed and leaves out entirely.
CLOSED = {"Completed", "Cancelled"}
# Statuses whose milestones are worth pulling (the page only shows milestones for upcoming projects).
MILESTONE_STATUSES = {
    "Active", "In Compliance", "Pending Welcome Call", "Pending Questionnaire Return",
    "Active -Dual Maintenance", "On Hold - Back to Sales", "On Hold Monthly Check In", "On Hold Weekly Check In",
}
# CRM stages for "Coming from sales" matching.
CRM_STAGES = ["Pending Welcome Call", "Welcome Call Complete", "First Check Date Push", "NCP Received/In Compliance Review"]
# CRM stages left out of the 60-day pipeline (already ran, closed or dead).
PIPELINE_EXCLUDE = {"Closed Ran", "Closed/Lost", "Closed/Won", "Client Cancelled", "First Check Date Ran",
                    "Not Sold", "Cold Lead", "Welcome Call Complete", "Welcome Call/Implementation"}

errors = []


def token():
    q = urllib.parse.urlencode({
        "refresh_token": os.environ["ZOHO_REFRESH_TOKEN"],
        "client_id": os.environ["ZOHO_CLIENT_ID"],
        "client_secret": os.environ["ZOHO_CLIENT_SECRET"],
        "grant_type": "refresh_token",
    }).encode()
    with urllib.request.urlopen(urllib.request.Request(f"{ACCOUNTS}/oauth/v2/token", data=q, method="POST"), timeout=30) as r:
        body = json.load(r)
    if "access_token" not in body:
        sys.exit(f"Zoho token refresh failed: {body}")
    return body["access_token"]


TOKEN = None


def call(url, params=None, body=None, retries=3):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"Authorization": f"Zoho-oauthtoken {TOKEN}", "Content-Type": "application/json"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 204:
                return {}
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(f"{e.code} from {url}: {e.read()[:300]!r}")


def as_list(resp, *keys):
    """Zoho wraps lists differently per API; find the first list."""
    if isinstance(resp, list):
        return resp
    for k in keys:
        if isinstance(resp, dict) and isinstance(resp.get(k), list):
            return resp[k]
    if isinstance(resp, dict):
        for v in resp.values():
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                inner = as_list(v, *keys)
                if inner:
                    return inner
    return []


def fetch_projects():
    out, page = [], 1
    while True:
        resp = call(f"{PROJECTS_API}/projects", {"page": page, "per_page": 100})
        batch = as_list(resp, "result", "projects")
        out.extend(batch)
        if len(batch) < 100 or page > 50:
            break
        page += 1
    keep = []
    for p in out:
        layout = (p.get("layout") or {}).get("id")
        status = str((p.get("status") or {}).get("name") or "").strip()
        if layout != LAYOUT or status in CLOSED or p.get("is_completed"):
            continue
        keep.append(p)
    return keep


def fetch_milestones(projects):
    rows, pulled = [], dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for p in projects:
        status = str((p.get("status") or {}).get("name") or "").strip()
        if status not in MILESTONE_STATUSES:
            continue
        try:
            resp = call(f"{PROJECTS_API}/projects/{p['id']}/phases", {"includable_fields": "percent_complete", "per_page": 100})
        except RuntimeError as e:
            errors.append(f"milestones {p.get('key')}: {e}")
            continue
        phases = as_list(resp, "phases", "result", "milestones")
        for i, ph in enumerate(phases):
            st = ph.get("status")
            st = st.get("name") if isinstance(st, dict) else st
            rows.append({
                "id": str(ph.get("id")), "project_key": p.get("key"), "project_id": str(p.get("id")),
                "order": ph.get("sequence") or i + 1, "milestone": ph.get("name"),
                "pct": int(float(ph.get("percent_complete") or 0)), "status": st or "Active", "pulled": pulled,
            })
    rows.sort(key=lambda r: (r["project_key"] or "", r["order"]))
    return rows


def coql(query):
    out, offset = [], 0
    while True:
        resp = call(f"{CRM_API}/coql", body={"select_query": f"{query} limit {offset}, 200"})
        batch = resp.get("data") or []
        out.extend(batch)
        if not (resp.get("info") or {}).get("more_records") or offset > 2000:
            return out
        offset += 200


def fetch_crm_deals():
    stages = ", ".join(f"'{s}'" for s in CRM_STAGES)
    rows = coql("select Deal_Name, Stage, Owner.last_name, Estimated_First_Check_Date, Run_Date, Number_of_Employees, "
                "Product_Bundle, NCP_Complete, Proposed_Welcome_Call_Date, Welcome_Call_Completed_Date "
                f"from Deals where Stage in ({stages})")
    return {"data": {"data": rows}}


def fetch_pipeline60():
    today = dt.date.today()
    end = today + dt.timedelta(days=60)
    rows = coql("select Deal_Name, Stage, Owner.first_name, Owner.last_name, Estimated_First_Check_Date, Actual_Ran, "
                "Number_of_Employees, Product_Bundle, Client_Type, NCP_Complete, Competition "
                f"from Deals where Estimated_First_Check_Date between '{today}' and '{end}'")
    out = []
    for d in rows:
        if d.get("Stage") in PIPELINE_EXCLUDE:
            continue
        owner = " ".join(x for x in [d.get("Owner.first_name"), d.get("Owner.last_name")] if x)
        out.append({
            "id": str(d.get("id")), "owner": owner or None, "deal": d.get("Deal_Name"), "stage": d.get("Stage"),
            "projected_revenue": d.get("Actual_Ran"), "first_check": d.get("Estimated_First_Check_Date"),
            "client_type": d.get("Client_Type") or "", "bundle": d.get("Product_Bundle"),
            "ncp_complete": "Yes" if d.get("NCP_Complete") else "No", "current_provider": d.get("Competition"),
            "employees": None if d.get("Number_of_Employees") is None else str(d.get("Number_of_Employees")),
        })
    return out


def safe(name, fn, *args, default=None):
    try:
        return fn(*args)
    except Exception as e:  # keep the rest of the page alive if one source fails
        errors.append(f"{name}: {e}")
        print(f"WARNING {name}: {e}", file=sys.stderr)
        return default


def main():
    global TOKEN
    TOKEN = token()
    projects = safe("projects", fetch_projects, default=None)
    if projects is None:
        sys.exit("Could not load Zoho Projects; leaving the last published data in place.")
    data = {
        "as_of": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zp_projects": {"data": {"result": projects}},
        "crm_deals": safe("crm_deals", fetch_crm_deals, default={"data": {"data": []}}),
        "pipeline60": safe("pipeline60", fetch_pipeline60, default=[]),
        "milestones": safe("milestones", fetch_milestones, projects, default=[]),
        "errors": errors,
    }
    with open(OUT, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    print(f"Wrote {len(projects)} projects, {len(data['milestones'])} milestones, "
          f"{len(data['crm_deals']['data']['data'])} CRM deals, {len(data['pipeline60'])} pipeline deals; {len(errors)} errors")


if __name__ == "__main__":
    main()
