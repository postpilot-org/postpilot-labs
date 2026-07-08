#!/usr/bin/env python3
"""
Klaviyo RFM Customer Scoring Audit — portable, multi-account.

Runs entirely from a Klaviyo PRIVATE API key. All customer data is pulled and
crunched locally; nothing is sent anywhere except back to Klaviyo's own API.

Why a script (and not the MCP connector): a full-base RFM audit means paginating
the entire Placed Order history plus every buyer profile. Through an MCP/chat
connector every row flows through the model's context and caps out almost
immediately. Run locally with an API key, the same job finishes in minutes.

Usage:
    python3 klaviyo_rfm_audit.py                 # first run: walks you through key setup
    python3 klaviyo_rfm_audit.py --site overtone # reuse a saved key for a named site
    python3 klaviyo_rfm_audit.py --window-months 36 --out ./out

Key resolution order:
    1. --api-key on the command line
    2. KLAVIYO_API_KEY environment variable
    3. saved key for --site (in ~/.config/klaviyo-rfm/keys.json, chmod 600)
    4. interactive setup flow (prints instructions, prompts, offers to save)

No third-party dependencies — standard library only.
"""

import argparse
import datetime as dt
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://a.klaviyo.com/api"
DEFAULT_REVISION = "2024-10-15"  # bump if Klaviyo deprecates; any recent dated revision works
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "klaviyo-rfm")
CONFIG_FILE = os.path.join(CONFIG_DIR, "keys.json")

# 8-segment RFM classifier — EXHAUSTIVE (every R,F,M lands in a named segment;
# there is no "Other"). Recency drives the split; Frequency and Monetary combine
# into a value tier v (1-5). Cold + high-value routes to Can't Lose Them / At Risk
# (the direct-mail money segments) rather than falling through a gap. Evaluated
# top-down; the final `else` guarantees full coverage.
def classify(r, f, m):
    # Two value notions: STRICT (high on both F and M) keeps the active top tiers
    # exclusive; LOOSE (high on EITHER F or M) makes the lapsed winback tiers
    # inclusive, so a frequent-but-cheap OR big-one-time buyer who's gone cold still
    # counts as Can't Lose Them.
    strict = (f + m) / 2.0
    loose = float(max(f, m))
    if r >= 4 and strict >= 4:
        return "Champions"                 # recent + high on both
    if r >= 4 and f <= 2 and loose <= 2.5:
        return "New Customers"             # recent, few orders, low value
    if r >= 3 and strict >= 3.5:
        return "Loyal Customers"           # recent-ish + solidly valuable
    if r >= 3:
        return "Potential Loyalists"       # recent-ish, still building
    if loose >= 4:
        return "Can't Lose Them"           # cold (r<=2), high on frequency OR spend
    if loose >= 2.5:
        return "At Risk"                   # cold but worth winning back
    if r == 1 and loose <= 1.5:
        return "Lost"                      # long gone, low value
    return "Hibernating"                   # cold, low value, not quite Lost


# --------------------------------------------------------------------------- #
# API-key setup
# --------------------------------------------------------------------------- #
SETUP_TEXT = """
================================================================================
  Klaviyo RFM Audit — API key setup
================================================================================
This script needs a Klaviyo PRIVATE API key (read-only is enough).

How to create one (takes ~1 minute):

  1. Log in to the Klaviyo account you want to audit.
  2. Go to:  Settings  ->  API keys
     (direct link: https://www.klaviyo.com/settings/account/api-keys )
  3. Click "Create Private API Key".
  4. Name it something like:  RFM Audit (read-only)
  5. Under scopes, choose "Select scopes" and grant READ access to:
        - Accounts
        - Metrics
        - Events
        - Profiles
     (Read-only is all this script uses. It never writes to your account.)
  6. Click Create, then copy the key. It starts with  pk_
================================================================================
"""


def load_config():
    try:
        with open(CONFIG_FILE, "r") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_key(site, key):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    cfg = load_config()
    cfg[site] = key
    with open(CONFIG_FILE, "w") as fh:
        json.dump(cfg, fh, indent=2)
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass
    print(f"  Saved key for site '{site}' to {CONFIG_FILE} (permissions 600).", file=sys.stderr)


def resolve_api_key(args):
    # 1. explicit flag
    if args.api_key:
        return args.api_key.strip()
    # 2. environment
    env = os.environ.get("KLAVIYO_API_KEY")
    if env:
        print("  Using key from KLAVIYO_API_KEY environment variable.", file=sys.stderr)
        return env.strip()
    # 3. saved site key
    if args.site:
        cfg = load_config()
        if args.site in cfg:
            print(f"  Using saved key for site '{args.site}'.", file=sys.stderr)
            return cfg[args.site].strip()
    # 4. interactive setup
    print(SETUP_TEXT)
    if not sys.stdin.isatty():
        print(
            "No API key found and no interactive terminal available.\n"
            "Provide one via --api-key, the KLAVIYO_API_KEY env var, or a saved --site.",
            file=sys.stderr,
        )
        sys.exit(2)
    key = input("Paste your Klaviyo private API key (pk_...): ").strip()
    if not key:
        print("No key entered. Exiting.", file=sys.stderr)
        sys.exit(2)
    if not key.startswith("pk_"):
        print("  Warning: private keys normally start with 'pk_'. Continuing anyway.", file=sys.stderr)
    site = args.site
    if not site:
        site = input("Optional: name this site to save the key for reuse (blank to skip): ").strip()
    if site:
        save_key(site, key)
    return key


# --------------------------------------------------------------------------- #
# HTTP with auth, revision header, and 429/5xx backoff
# --------------------------------------------------------------------------- #
def api_request(method, path, key, revision, params=None, body=None, max_retries=6):
    url = path if path.startswith("http") else f"{API_BASE}{path}"
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, safe="(),[]-\":")
    headers = {
        "Authorization": f"Klaviyo-API-Key {key}",
        "revision": revision,
        "accept": "application/json",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["content-type"] = "application/json"

    attempt = 0
    while True:
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            payload = e.read().decode("utf-8", "replace")
            if e.code == 401:
                print("\nAPI key rejected (401). Check the key and its scopes.", file=sys.stderr)
                sys.exit(1)
            if e.code in (429, 500, 502, 503, 504) and attempt < max_retries:
                retry_after = e.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else min(2 ** attempt, 30)
                time.sleep(wait)
                attempt += 1
                continue
            print(f"\nHTTP {e.code} on {url}\n{payload}", file=sys.stderr)
            sys.exit(1)
        except urllib.error.URLError as e:
            if attempt < max_retries:
                time.sleep(min(2 ** attempt, 30))
                attempt += 1
                continue
            print(f"\nNetwork error on {url}: {e}", file=sys.stderr)
            sys.exit(1)


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def get_account_name(key, revision):
    r = api_request("GET", "/accounts/", key, revision)
    try:
        return r["data"][0]["attributes"]["contact_information"]["organization_name"] or "Your account"
    except (KeyError, IndexError, TypeError):
        return "Your account"


def find_placed_order_metric(key, revision):
    """Return (metric_id, metric_name). Prefer an ecommerce 'Placed Order'."""
    candidates = []
    url = "/metrics/"
    params = {"fields[metric]": "name,integration"}
    while url:
        r = api_request("GET", url, key, revision, params=params)
        for m in r["data"]:
            name = m["attributes"].get("name", "")
            integ = (m["attributes"].get("integration") or {}).get("name", "")
            candidates.append((m["id"], name, integ))
        url = r.get("links", {}).get("next")
        params = None
    # exact "Placed Order" first, then anything containing it
    for want in ("placed order",):
        for mid, name, integ in candidates:
            if name.strip().lower() == want:
                return mid, name
    for mid, name, integ in candidates:
        if "placed order" in name.lower() or "ordered product" in name.lower():
            return mid, name
    # give up gracefully
    print("\nCould not auto-detect a 'Placed Order' metric. Available metrics:", file=sys.stderr)
    for mid, name, integ in candidates[:40]:
        print(f"  {mid}  {name}  [{integ}]", file=sys.stderr)
    print("Re-run with --metric-id <ID>.", file=sys.stderr)
    sys.exit(1)


# --------------------------------------------------------------------------- #
# Event walk  ->  Recency + in-window frequency
# --------------------------------------------------------------------------- #
def walk_events(key, revision, metric_id, since_iso, page_size=200):
    """Return {profile_id: {'last': datetime, 'orders_in_window': int}}."""
    buyers = {}
    params = {
        "filter": f'equals(metric_id,"{metric_id}"),greater-or-equal(datetime,{since_iso})',
        "sort": "-datetime",
        "fields[event]": "datetime",
        "page[size]": str(page_size),
    }
    url = "/events/"
    pages = 0
    events = 0
    while url:
        r = api_request("GET", url, key, revision, params=params)
        params = None
        for ev in r["data"]:
            when = ev["attributes"]["datetime"]
            pid = (((ev.get("relationships") or {}).get("profile") or {}).get("data") or {}).get("id")
            if not pid:
                continue
            when_dt = _parse_dt(when)
            rec = buyers.get(pid)
            if rec is None:
                buyers[pid] = {"last": when_dt, "orders_in_window": 1}
            else:
                rec["orders_in_window"] += 1
                if when_dt > rec["last"]:
                    rec["last"] = when_dt
            events += 1
        pages += 1
        if pages % 20 == 0:
            print(f"    ...{pages} event pages, {events} orders, {len(buyers)} buyers so far", file=sys.stderr)
        url = r.get("links", {}).get("next")
    print(f"    event walk done: {events} orders across {len(buyers)} in-window buyers ({pages} pages)", file=sys.stderr)
    return buyers


# --------------------------------------------------------------------------- #
# Profile enrichment  ->  Monetary + all-time frequency
# --------------------------------------------------------------------------- #
def enrich(key, revision, profile_ids):
    """Return {profile_id: {'clv','orders','aov'}} from predictive analytics."""
    out = {}
    ids = list(profile_ids)
    fields = (
        "predictive_analytics.historic_clv,"
        "predictive_analytics.historic_number_of_orders,"
        "predictive_analytics.average_order_value"
    )
    total = len(ids)
    for i in range(0, total, 100):
        batch = ids[i : i + 100]
        quoted = ",".join(f'"{b}"' for b in batch)
        params = {
            "filter": f"any(id,[{quoted}])",
            "additional-fields[profile]": "predictive_analytics",
            "fields[profile]": fields,
            "page[size]": "100",
        }
        r = api_request("GET", "/profiles/", key, revision, params=params)
        for p in r["data"]:
            pa = (p["attributes"].get("predictive_analytics") or {})
            out[p["id"]] = {
                "clv": pa.get("historic_clv"),
                "orders": pa.get("historic_number_of_orders"),
                "aov": pa.get("average_order_value"),
            }
        if (i // 100 + 1) % 10 == 0:
            print(f"    ...enriched {min(i + 100, total)}/{total} buyers", file=sys.stderr)
    print(f"    enrichment done: {len(out)} buyers", file=sys.stderr)
    return out


# --------------------------------------------------------------------------- #
# Sampled READ-ONLY mode: representative sample + true purchase recency,
# checkpointed to disk so it survives short command windows.
# --------------------------------------------------------------------------- #
def _account_created_span(key, revision):
    """(earliest_created_iso, now_iso) to stratify the sample across signup history."""
    now = dt.datetime.now(dt.timezone.utc)
    r = api_request("GET", "/profiles/", key, revision,
                    params={"sort": "created", "fields[profile]": "created", "page[size]": "1"})
    try:
        earliest = _parse_dt(r["data"][0]["attributes"]["created"])
    except Exception:
        earliest = now - dt.timedelta(days=365 * 5)
    return earliest, now


def _window_draws(series, earliest, now, windows, target_n, floor=6):
    """Split [earliest, now] into `windows` equal-time signup windows and allocate
    per-window sample quotas PROPORTIONAL to order volume in each window (a proxy
    for how many buyers are active from each era). Corrects the equal-weight bias
    that over-samples thin early cohorts. Returns a list of per-window quotas."""
    span = (now - earliest).total_seconds()
    edges = [earliest + dt.timedelta(seconds=span * w / windows) for w in range(windows + 1)]
    vol = [0.0] * windows
    for mdt, c in series:
        for w in range(windows):
            if edges[w] <= mdt < edges[w + 1]:
                vol[w] += c
                break
    total = sum(vol) or 1.0
    draws = [max(int(round(target_n * v / total)), floor) for v in vol]
    return draws


def stratified_buyer_sample(key, revision, target_n, state, save, windows=None, window_draws=None):
    """Pull buyers spread across the account's created-date timeline, read-only.
    If window_draws is given, each window's quota is volume-weighted (representative);
    otherwise quotas are equal. Resumable: checkpoints after every window via save()."""
    earliest, now = _account_created_span(key, revision)
    span = (now - earliest).total_seconds()
    if window_draws:
        windows = len(window_draws)
    if windows is None:
        windows = min(max(24, -(-target_n // 80)), 60)
    equal_per_window = max(target_n // windows + 1, 25)
    fields = ("predictive_analytics.historic_clv,predictive_analytics.historic_number_of_orders,"
              "predictive_analytics.average_order_value")
    collected = {b["id"]: b for b in state.get("sample", [])}
    start = state.get("sample_windows_done", 0)
    for w in range(start, windows):
        w0 = earliest + dt.timedelta(seconds=span * w / windows)
        w1 = earliest + dt.timedelta(seconds=span * (w + 1) / windows)
        params = {
            "additional-fields[profile]": "predictive_analytics",
            "fields[profile]": fields,
            "filter": f"greater-than(created,{w0.strftime('%Y-%m-%dT%H:%M:%S')}),less-than(created,{w1.strftime('%Y-%m-%dT%H:%M:%S')})",
            "page[size]": "100",
        }
        quota = window_draws[w] if window_draws else equal_per_window
        got = 0
        url = "/profiles/"
        pages_in_window = 0
        max_pages = max(4, -(-quota // 100) + 1)
        while url and got < quota and pages_in_window < max_pages:
            try:
                r = api_request("GET", url, key, revision, params=params)
            except SystemExit:
                break
            params = None
            for p in r["data"]:
                pa = p["attributes"].get("predictive_analytics") or {}
                if (pa.get("historic_number_of_orders") or 0) > 0 and pa.get("historic_clv") is not None:
                    collected[p["id"]] = {"id": p["id"], "clv": pa["historic_clv"],
                                          "orders": pa["historic_number_of_orders"], "aov": pa.get("average_order_value") or 0}
                    got += 1
                    if got >= quota:
                        break
            url = r.get("links", {}).get("next")
            pages_in_window += 1
        state["sample"] = list(collected.values())
        state["sample_windows_done"] = w + 1
        save()
        print(f"    sampling window {w+1}/{windows} — {len(collected)} buyers collected", file=sys.stderr)
        if len(collected) >= target_n:
            break
    state["sampling_complete"] = True
    save()
    return state["sample"]


def monthly_order_series(key, revision, metric_id, start_year):
    """Read-only monthly Placed Order volume across all history. Returns
    (series, total_orders, total_revenue) where series = [(month_start_dt, count), ...].
    count and sum_value are summable across intervals, so totals are exact."""
    now = dt.datetime.now(dt.timezone.utc)
    series = []
    tot_orders = 0.0
    tot_rev = 0.0
    for yr in range(start_year, now.year + 1):
        body = {"data": {"type": "metric-aggregate", "attributes": {
            "metric_id": metric_id, "measurements": ["count", "sum_value"], "interval": "month",
            "filter": [f"greater-or-equal(datetime,{yr}-01-01T00:00:00)", f"less-than(datetime,{yr+1}-01-01T00:00:00)"],
            "timezone": "UTC"}}}
        try:
            r = api_request("POST", "/metric-aggregates/", key, revision, body=body)
            data = (r.get("data") or r.get("result", {}).get("data") or {})
            meas = data["attributes"]["data"][0]["measurements"]
            counts = meas.get("count") or []
            vals = meas.get("sum_value") or []
            for i, c in enumerate(counts):
                series.append((dt.datetime(yr, i + 1, 1, tzinfo=dt.timezone.utc), c))
            tot_orders += sum(counts)
            tot_rev += sum(vals)
        except SystemExit:
            continue
    return series, int(tot_orders), round(tot_rev, 2)


def alltime_totals(key, revision, metric_id, start_year):
    _, o, r = monthly_order_series(key, revision, metric_id, start_year)
    return o, r


def recency_lookup_one(key, revision, metric_id, pid):
    """True last Placed Order datetime for one profile (read-only), or None."""
    import urllib.parse
    f = urllib.parse.quote(f'equals(metric_id,"{metric_id}"),equals(profile_id,"{pid}")', safe="")
    url = f"/events/?filter={f}&sort=-datetime&fields%5Bevent%5D=datetime&page%5Bsize%5D=1"
    r = api_request("GET", url, key, revision)
    d = r.get("data") or r.get("result", {}).get("data") or []
    if d:
        return d[0]["attributes"]["datetime"]
    return None


def has_event_since(key, revision, metric_id, pid, since_iso):
    """1 if the profile has >=1 event of this metric since since_iso, else 0. Read-only."""
    import urllib.parse
    f = urllib.parse.quote(f'equals(metric_id,"{metric_id}"),equals(profile_id,"{pid}"),greater-or-equal(datetime,{since_iso})', safe="")
    url = f"/events/?filter={f}&fields%5Bevent%5D=datetime&page%5Bsize%5D=1"
    r = api_request("GET", url, key, revision)
    return 1 if (r.get("data") or []) else 0


def measure_engagement_sampled(key, revision, metrics, ids, since_iso, state, save, workers):
    """Per-buyer open/click/on-site existence checks (parallel, checkpointed)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    eng = state.setdefault("engagement", {})
    kinds = [k for k in ("open", "click", "onsite") if metrics.get(k)]
    todo = [pid for pid in ids if pid not in eng]
    if not todo or not kinds:
        return eng, kinds
    print(f"  Measuring engagement (open/click/on-site, last 90d) for {len(todo)} buyers ({workers} parallel)...", file=sys.stderr)

    def work(pid):
        # resilient: on any error (incl. rate-limit exhaustion) return None so the
        # buyer is marked attempted and excluded from rates, never retried forever.
        try:
            return pid, {k: has_event_since(key, revision, metrics[k], pid, since_iso) for k in kinds}
        except BaseException:
            return pid, None

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(work, pid) for pid in todo]
        for fut in as_completed(futs):
            try:
                pid, flags = fut.result()
            except BaseException:
                continue
            eng[pid] = flags  # dict of flags, or None if the lookup failed
            done += 1
            if done % 100 == 0:
                save()
                print(f"    engagement {done}/{len(todo)}", file=sys.stderr)
    save()
    return eng, kinds


def run_audit_sampled(key, revision, metric_id, out_dir, target_n, window_months, workers=8,
                      engagement=True, engagement_days=90):
    """Read-only sampled RFM. Checkpoints to <out_dir>/.rfm_state.json and resumes."""
    os.makedirs(out_dir, exist_ok=True)
    state_path = os.path.join(out_dir, ".rfm_state.json")
    try:
        with open(state_path) as fh:
            state = json.load(fh)
        print(f"  Resuming from checkpoint ({len(state.get('recency', {}))}/{len(state.get('sample', []))} recency done).", file=sys.stderr)
    except (OSError, json.JSONDecodeError):
        state = {"sample": [], "recency": {}}

    def save():
        with open(state_path, "w") as fh:
            json.dump(state, fh)

    # order-volume series + exact totals (once; cached in state for resumes)
    earliest, now = _account_created_span(key, revision)
    if "totals" not in state:
        series, tot_orders, tot_rev = monthly_order_series(key, revision, metric_id, earliest.year)
        state["totals"] = {"orders": tot_orders, "revenue": tot_rev,
                           "series": [(d.isoformat(), c) for d, c in series]}
        save()
    tot_orders = state["totals"]["orders"]
    tot_rev = state["totals"]["revenue"]
    series = [(dt.datetime.fromisoformat(d), c) for d, c in state["totals"]["series"]]

    # AUTO-SIZE: census small accounts, fixed ~1500 otherwise (sample size for a
    # proportion is ~independent of population size once it's large).
    if target_n <= 0:
        probe = api_request("GET", "/profiles/", key, revision, params={
            "additional-fields[profile]": "predictive_analytics",
            "fields[profile]": "predictive_analytics.historic_number_of_orders", "page[size]": "100"})
        oc = [(p["attributes"].get("predictive_analytics") or {}).get("historic_number_of_orders") or 0
              for p in probe["data"]]
        oc = [o for o in oc if o > 0]
        mean_o = (sum(oc) / len(oc)) if oc else 1.8
        est_buyers = int(tot_orders / mean_o) if tot_orders else 0
        if est_buyers and est_buyers <= 4000:
            target_n = est_buyers            # census the whole (small) base
            print(f"  Auto-size: ~{est_buyers:,} buyers -> census (sampling the full base).", file=sys.stderr)
        else:
            target_n = 1500
            print(f"  Auto-size: ~{est_buyers:,} buyers -> sample {target_n} (precision ~±2.5%, independent of base size).", file=sys.stderr)

    if not state.get("sampling_complete"):
        windows = min(max(24, -(-target_n // 80)), 60)
        draws = _window_draws(series, earliest, now, windows, target_n)
        print(f"  Building a representative sample (~{target_n} buyers, {windows} signup windows, "
              f"volume-weighted so eras are represented in proportion to their buyer activity)...", file=sys.stderr)
        stratified_buyer_sample(key, revision, target_n, state, save, window_draws=draws)

    todo = [b for b in state["sample"] if b["id"] not in state["recency"]]
    if todo:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        print(f"  Looking up true last-order date for {len(todo)} buyers ({workers} parallel, read-only)...", file=sys.stderr)
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(recency_lookup_one, key, revision, metric_id, b["id"]): b["id"] for b in todo}
            for fut in as_completed(futs):
                pid = futs[fut]
                try:
                    state["recency"][pid] = fut.result()
                except BaseException:
                    state["recency"][pid] = None
                done += 1
                if done % 50 == 0:
                    save()
                    print(f"    recency {done}/{len(todo)}", file=sys.stderr)
        save()

    # build rows and score. In sampled mode we already have each buyer's TRUE
    # last-order date, so we score every sampled buyer (long-lapsed ones bin to
    # Lost / Hibernating / Can't Lose via a low recency score) rather than
    # dropping them — that keeps the sample representative of the whole base.
    today = dt.datetime.now(dt.timezone.utc)
    rows = []
    for b in state["sample"]:
        last = state["recency"].get(b["id"])
        if not last:
            continue  # buyer with no Placed Order event (rare data mismatch)
        last_dt = _parse_dt(last)
        rows.append({"id": b["id"], "days_ago": (today - last_dt).days,
                     "orders": b["orders"], "clv": b["clv"], "aov": b["aov"],
                     "orders_in_window": b["orders"]})
    if not rows:
        print("No buyers with a purchase event in the sample.", file=sys.stderr)
        sys.exit(1)

    r_score = quintile_scorer([x["days_ago"] for x in rows], reverse=True)
    m_score = quintile_scorer([x["clv"] for x in rows])
    order_vals = [x["orders"] for x in rows]
    ones = sum(1 for o in order_vals if o == 1)
    skewed = ones / max(len(order_vals), 1) > 0.5
    f_score = (lambda o: f_score_fallback(o)) if skewed else quintile_scorer(order_vals)
    seg_stats = {}
    id_to_seg = {}
    for x in rows:
        rs, fs, ms = r_score(x["days_ago"]), f_score(x["orders"]), m_score(x["clv"])
        seg = classify(rs, fs, ms)
        id_to_seg[x["id"]] = seg
        s = seg_stats.setdefault(seg, {"n": 0, "clv": [], "orders": [], "days": [], "aov": [], "r": [], "f": [], "m": [], "v": []})
        s["n"] += 1
        for kk, vv in (("clv", x["clv"]), ("orders", x["orders"]), ("days", x["days_ago"]),
                       ("aov", x["aov"]), ("r", rs), ("f", fs), ("m", ms), ("v", max(fs, ms))):
            s[kk].append(vv)
    max_days = max(x["days_ago"] for x in rows)
    span_months = round(max_days / 30.44, 1)
    since_iso = (today - dt.timedelta(days=max_days)).strftime("%Y-%m-%dT%H:%M:%S")
    result = _summarize(rows, seg_stats, skewed, span_months, since_iso)

    # --- extrapolate the sample up to the full customer file ---
    sample_n = len(rows)
    mean_orders = statistics.mean(x["orders"] for x in rows) or 1
    total_buyers = int(round(tot_orders / mean_orders)) if tot_orders else sample_n
    scale = total_buyers / sample_n if sample_n else 1
    print(f"  Extrapolating: {sample_n} sampled -> est. {total_buyers:,} total buyers "
          f"(all-time orders {tot_orders:,} / {mean_orders:.1f} avg orders per buyer).", file=sys.stderr)

    for s in result["segments"].values():
        s["count"] = int(round(s["count"] * scale))
        s["sum_clv"] = round(s["avg_clv"] * s["count"], 2)
    seg_rev = sum(s["sum_clv"] for s in result["segments"].values()) or 1
    for s in result["segments"].values():
        s["pct_of_revenue"] = round(100 * s["sum_clv"] / seg_rev, 1)

    result["buyers_scored"] = total_buyers
    result["total_revenue"] = tot_rev or round(seg_rev, 2)
    if tot_orders:
        result["overall_aov"] = round(tot_rev / tot_orders, 2)
    result["direct_mail"] = _direct_mail(result["segments"], result["overall_aov"])
    result["sampled"] = True
    result["sample_n"] = sample_n
    result["sample_note"] = (f"Extrapolated from a read-only representative sample of {sample_n:,} buyers to the "
                             f"full base of ~{total_buyers:,} (all-time orders ÷ avg orders per buyer). Recency is "
                             f"each buyer's true last-purchase date. Segment counts and the direct-mail opportunity "
                             f"are scaled to the full file; the gap ratio and mix are measured on the sample.")

    # --- engagement by segment (email open/click + on-site), per-buyer, read-only ---
    if engagement:
        metrics = find_engagement_metrics(key, revision)
        eng_since = (today - dt.timedelta(days=engagement_days)).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")
        eng, kinds = measure_engagement_sampled(key, revision, metrics, list(id_to_seg), eng_since, state, save, workers)
        if kinds:
            tally = {}
            for pid, seg in id_to_seg.items():
                fl = eng.get(pid)
                if not fl:  # not measured (missing or failed) — exclude from rates
                    continue
                t = tally.setdefault(seg, {"open": 0, "click": 0, "onsite": 0, "base": 0})
                t["base"] += 1
                for k in kinds:
                    t[k] += fl.get(k, 0)
            key_map = {"open": "open_rate_90d", "click": "click_rate_90d", "onsite": "onsite_rate_90d"}
            for seg, s in result["segments"].items():
                t = tally.get(seg)
                if t and t["base"]:
                    for k in kinds:
                        s[key_map[k]] = round(100 * t[k] / t["base"], 1)
            result["engagement_days"] = engagement_days
    return result


# --------------------------------------------------------------------------- #
# Engagement (email opens/clicks + on-site) by segment
# --------------------------------------------------------------------------- #
ENGAGEMENT_NAMES = {
    "open": ("opened email",),
    "click": ("clicked email",),
    "onsite": ("active on site",),
}


def find_engagement_metrics(key, revision):
    """Return {'open':id|None, 'click':id|None, 'onsite':id|None}."""
    found = {"open": None, "click": None, "onsite": None}
    url = "/metrics/"
    params = {"fields[metric]": "name"}
    all_metrics = []
    while url:
        r = api_request("GET", url, key, revision, params=params)
        all_metrics += [(m["id"], m["attributes"].get("name", "").strip().lower()) for m in r["data"]]
        url = r.get("links", {}).get("next")
        params = None
    for kind, names in ENGAGEMENT_NAMES.items():
        for mid, name in all_metrics:
            if name in names:
                found[kind] = mid
                break
    return found


def walk_metric_profile_ids(key, revision, metric_id, since_iso, keep, label):
    """Return the subset of `keep` (a set of profile IDs) that has >=1 event of
    this metric since `since_iso`. Walks the event stream, slim, intersecting."""
    hits = set()
    params = {
        "filter": f'equals(metric_id,"{metric_id}"),greater-or-equal(datetime,{since_iso})',
        "sort": "-datetime",
        "fields[event]": "datetime",
        "page[size]": "200",
    }
    url = "/events/"
    pages = 0
    while url:
        r = api_request("GET", url, key, revision, params=params)
        params = None
        for ev in r["data"]:
            pid = (((ev.get("relationships") or {}).get("profile") or {}).get("data") or {}).get("id")
            if pid in keep:
                hits.add(pid)
        pages += 1
        if pages % 25 == 0:
            print(f"    ...{label}: {pages} pages, {len(hits)}/{len(keep)} buyers matched", file=sys.stderr)
        # early exit: every buyer already matched
        if len(hits) == len(keep):
            break
        url = r.get("links", {}).get("next")
    print(f"    {label}: {len(hits)}/{len(keep)} buyers engaged ({pages} pages)", file=sys.stderr)
    return hits


def measure_engagement(key, revision, id_to_seg, since_iso):
    """Return {segment: {'open':n,'click':n,'onsite':n,'base':n}} plus which kinds ran."""
    metrics = find_engagement_metrics(key, revision)
    buyers = set(id_to_seg)
    tally = {}
    for seg in set(id_to_seg.values()):
        tally[seg] = {"open": 0, "click": 0, "onsite": 0, "base": 0}
    for pid, seg in id_to_seg.items():
        tally[seg]["base"] += 1
    ran = []
    for kind in ("open", "click", "onsite"):
        mid = metrics.get(kind)
        if not mid:
            print(f"    (no metric found for {kind}; skipping)", file=sys.stderr)
            continue
        hits = walk_metric_profile_ids(key, revision, mid, since_iso, buyers, kind)
        ran.append(kind)
        for pid in hits:
            tally[id_to_seg[pid]][kind] += 1
    return tally, ran


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def quintile_scorer(values, reverse=False):
    """Return a function mapping a value -> 1..5 by quintile.
    reverse=True means lower raw value scores higher (used for recency days-ago)."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return lambda x: 3
    cuts = [_pct(vals, p) for p in (20, 40, 60, 80)]

    def score(x):
        if x is None:
            return 1
        s = 1
        for c in cuts:
            if x > c:
                s += 1
        return min(s, 5)

    if not reverse:
        return score
    return lambda x: 6 - score(x)  # flip so low days-ago -> 5


def _pct(sorted_vals, p):
    if not sorted_vals:
        return 0
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def f_score_fallback(orders):
    if orders is None:
        return 1
    if orders >= 6:
        return 5
    if orders >= 4:
        return 4
    return max(1, min(orders, 3))  # 1->1, 2->2, 3->3


def run_audit(key, revision, metric_id, window_months, sample, engagement=True, engagement_days=90):
    today = dt.datetime.now(dt.timezone.utc)
    since = (today - dt.timedelta(days=int(window_months * 30.44))).replace(microsecond=0)
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%S")
    print(f"  Recency window: last {window_months} months (since {since_iso})", file=sys.stderr)

    print("  Step 1/3: walking Placed Order event stream...", file=sys.stderr)
    buyers = walk_events(key, revision, metric_id, since_iso)
    if not buyers:
        print("No in-window orders found. Try a larger --window-months.", file=sys.stderr)
        sys.exit(1)

    ids = list(buyers.keys())
    if sample and sample < len(ids):
        import random

        random.seed(42)
        ids = random.sample(ids, sample)
        print(f"  (sampling {sample} of {len(buyers)} buyers for enrichment)", file=sys.stderr)

    print("  Step 2/3: enriching buyers with predictive analytics (CLV / orders)...", file=sys.stderr)
    profiles = enrich(key, revision, ids)

    print("  Step 3/3: scoring RFM and binning segments...", file=sys.stderr)
    rows = []
    for pid in ids:
        b = buyers[pid]
        pa = profiles.get(pid, {})
        clv = pa.get("clv")
        orders = pa.get("orders")
        aov = pa.get("aov")
        days_ago = (today - b["last"]).days
        rows.append(
            {
                "id": pid,
                "days_ago": days_ago,
                "orders": orders if orders is not None else b["orders_in_window"],
                "clv": clv if clv is not None else 0.0,
                "aov": aov if aov is not None else 0.0,
                "orders_in_window": b["orders_in_window"],
            }
        )

    # quintile scorers
    r_score = quintile_scorer([x["days_ago"] for x in rows], reverse=True)
    order_vals = [x["orders"] for x in rows]
    m_score = quintile_scorer([x["clv"] for x in rows])
    # detect F skew (typical: most buyers = 1 order) -> use fixed brackets
    ones = sum(1 for o in order_vals if o == 1)
    skewed = ones / max(len(order_vals), 1) > 0.5
    f_score = (lambda o: f_score_fallback(o)) if skewed else quintile_scorer(order_vals)

    seg_stats = {}
    for x in rows:
        rs, fs, ms = r_score(x["days_ago"]), f_score(x["orders"]), m_score(x["clv"])
        seg = classify(rs, fs, ms)
        x["R"], x["F"], x["M"], x["segment"] = rs, fs, ms, seg
        s = seg_stats.setdefault(seg, {"n": 0, "clv": [], "orders": [], "days": [], "aov": [], "r": [], "f": [], "m": [], "v": []})
        s["n"] += 1
        s["clv"].append(x["clv"])
        s["orders"].append(x["orders"])
        s["days"].append(x["days_ago"])
        s["aov"].append(x["aov"])
        s["r"].append(rs)
        s["f"].append(fs)
        s["m"].append(ms)

    eng_tally, eng_ran = None, []
    if engagement:
        print(f"  Step 4/4: measuring email + on-site engagement (last {engagement_days} days)...", file=sys.stderr)
        print("    (this walks the opens/clicks/on-site streams; on heavy email senders it's the slowest part — skip with --no-engagement)", file=sys.stderr)
        eng_since = (today - dt.timedelta(days=engagement_days)).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")
        id_to_seg = {x["id"]: x["segment"] for x in rows}
        eng_tally, eng_ran = measure_engagement(key, revision, id_to_seg, eng_since)

    return _summarize(rows, seg_stats, skewed, window_months, since_iso, eng_tally, eng_ran, engagement_days)


# Direct-mail benchmarks (PostPilot platform medians). Edit to taste.
DM_RATE_CANT_LOSE = 0.137   # top-quartile response for warmest winback
DM_RATE_AT_RISK = 0.076     # median winback response
DM_COST_CANT_LOSE = 0.64    # 4x6 postcard
DM_COST_AT_RISK = 0.64      # 4x6 postcard


def _direct_mail(segments, overall_aov):
    def scenario(seg, rate, cost):
        s = segments.get(seg)
        if not s or s["count"] == 0:
            return None
        aud = s["count"]
        aov = s["avg_aov"] or overall_aov
        rev = aud * rate * aov
        c = aud * cost
        return {
            "audience": aud,
            "response_rate": rate,
            "aov": round(aov, 2),
            "cost_per_piece": cost,
            "revenue": round(rev, 2),
            "cost": round(c, 2),
            "net": round(rev - c, 2),
            "roas": round(rev / c, 1) if c else None,
        }

    cl = scenario("Can't Lose Them", DM_RATE_CANT_LOSE, DM_COST_CANT_LOSE)
    ar = scenario("At Risk", DM_RATE_AT_RISK, DM_COST_AT_RISK)
    combined_net = round(sum(x["net"] for x in (cl, ar) if x), 2)

    # Evergreen flow: the natural monthly lapse rate refills the At Risk + Can't Lose pool.
    evergreen = None
    present = [x for x in (cl, ar) if x]
    total_aud = sum(x["audience"] for x in present)
    if total_aud:
        monthly_volume = round(total_aud / 12)
        blended_rate = sum(x["audience"] * x["response_rate"] for x in present) / total_aud
        blended_aov = sum(x["audience"] * x["aov"] for x in present) / total_aud
        blended_cost = sum(x["audience"] * x["cost_per_piece"] for x in present) / total_aud
        monthly_net = monthly_volume * (blended_rate * blended_aov - blended_cost)
        evergreen = {
            "monthly_volume": monthly_volume,
            "blended_response": round(blended_rate, 3),
            "blended_aov": round(blended_aov, 2),
            "monthly_net": round(monthly_net, 2),
            "annualized_net": round(monthly_net * 12, 2),
            "forecast_cumulative": [round(monthly_net * m, 2) for m in range(1, 13)],
        }
    return {
        "cant_lose_them": cl,
        "at_risk": ar,
        "combined_one_time_net": combined_net,
        "evergreen": evergreen,
        "assumptions": {
            "cant_lose_response": DM_RATE_CANT_LOSE,
            "at_risk_response": DM_RATE_AT_RISK,
            "cant_lose_cost_per_piece": DM_COST_CANT_LOSE,
            "at_risk_cost_per_piece": DM_COST_AT_RISK,
            "source": "PostPilot platform medians across thousands of brands and winback campaigns in our dataset",
        },
    }


def _lorenz(clvs_sorted_desc):
    """Cumulative revenue share by customer percentile (richest first). Sampled
    finely — every 1% through the top decile, then every 2% — so the steep top of
    the curve is drawn accurately instead of as one straight chord to the 10% point."""
    import itertools
    total = sum(clvs_sorted_desc)
    n = len(clvs_sorted_desc)
    pts = [{"cust_pct": 0, "rev_pct": 0.0}]
    if not total or not n:
        return pts
    prefix = list(itertools.accumulate(clvs_sorted_desc))
    grid = list(range(1, 11)) + list(range(12, 101, 2))  # 1..10 by 1, then 12..100 by 2
    for cp in grid:
        idx = min(max(1, int(round(n * cp / 100))), n)
        pts.append({"cust_pct": cp, "rev_pct": round(100 * prefix[idx - 1] / total, 1)})
    return pts


def _summarize(rows, seg_stats, skewed, window_months, since_iso, eng_tally=None, eng_ran=None, engagement_days=90):
    clvs = sorted((x["clv"] for x in rows))
    clvs_desc = clvs[::-1]
    total_rev = sum(clvs)
    total_orders = sum(x["orders"] for x in rows)
    n = len(rows)
    overall_aov = round(total_rev / total_orders, 2) if total_orders else 0
    top_decile_cut = _pct(clvs, 90)
    median = _pct(clvs, 50)
    bottom_q_cut = _pct(clvs, 25)
    top_decile = [c for c in clvs if c >= top_decile_cut]
    bottom_q = [c for c in clvs if c <= bottom_q_cut]
    top_decile_avg = statistics.mean(top_decile) if top_decile else 0
    bottom_q_avg = statistics.mean(bottom_q) if bottom_q else 0
    top_decile_rev = sum(top_decile)

    segments = {}
    for seg, s in seg_stats.items():
        segments[seg] = {
            "count": s["n"],
            "pct_of_buyers": round(100 * s["n"] / n, 1),
            "avg_clv": round(statistics.mean(s["clv"]), 2) if s["clv"] else 0,
            "median_clv": round(statistics.median(s["clv"]), 2) if s["clv"] else 0,
            "avg_orders": round(statistics.mean(s["orders"]), 2) if s["orders"] else 0,
            "avg_days_since": round(statistics.mean(s["days"])) if s["days"] else 0,
            "avg_aov": round(statistics.mean([a for a in s["aov"] if a]), 2) if any(s["aov"]) else 0,
            "sum_clv": round(sum(s["clv"]), 2),
            "pct_of_revenue": round(100 * sum(s["clv"]) / total_rev, 1) if total_rev else 0,
            "avg_r": round(statistics.mean(s["r"]), 2) if s.get("r") else 0,
            "avg_f": round(statistics.mean(s["f"]), 2) if s.get("f") else 0,
            "avg_m": round(statistics.mean(s["m"]), 2) if s.get("m") else 0,
            "avg_v": round(statistics.mean(s["v"]), 2) if s.get("v") else 0,
        }

    # merge engagement rates (share of each segment with an event in the window)
    if eng_tally and eng_ran:
        kind_key = {"open": "open_rate_90d", "click": "click_rate_90d", "onsite": "onsite_rate_90d"}
        for seg, sd in segments.items():
            t = eng_tally.get(seg)
            base = (t or {}).get("base", 0)
            if not base:
                continue
            for kind in eng_ran:
                sd[kind_key[kind]] = round(100 * t[kind] / base, 1)

    return {
        "window_months": window_months,
        "window_since": since_iso,
        "engagement_days": engagement_days if (eng_tally and eng_ran) else None,
        "buyers_scored": n,
        "total_revenue": round(total_rev, 2),
        "overall_aov": overall_aov,
        "f_bracket_fallback_used": skewed,
        "gap": {
            "top_decile_avg_clv": round(top_decile_avg, 2),
            "median_clv": round(median, 2),
            "bottom_quartile_avg_clv": round(bottom_q_avg, 2),
            "gap_ratio_top_decile_vs_median": round(top_decile_avg / median, 1) if median else None,
            "gap_ratio_top_decile_vs_bottom_q": round(top_decile_avg / bottom_q_avg, 1) if bottom_q_avg else None,
            "top_decile_pct_of_revenue": round(100 * top_decile_rev / total_rev, 1) if total_rev else 0,
        },
        "lorenz": _lorenz(clvs_desc),
        "direct_mail": _direct_mail(segments, overall_aov),
        "segments": segments,
    }


def _parse_dt(s):
    s = s.replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(s)
    except ValueError:
        return dt.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)


# --------------------------------------------------------------------------- #
# HTML report (self-contained, inline SVG, no external deps or fonts)
# --------------------------------------------------------------------------- #
PALETTE = {
    "ink": "#2E2F34", "paper": "#F3F0EC", "card": "#FFFFFF", "lime": "#D0F582",
    "blue": "#6AB1F3", "lightblue": "#BBDEFF", "navy": "#398BC7", "muted": "#8A8B90",
}
SEG_ORDER = [
    "Champions", "Loyal Customers", "Potential Loyalists", "New Customers",
    "At Risk", "Can't Lose Them", "Hibernating", "Lost", "Other / Mid-tier",
]
DM_SEGS = {"At Risk", "Can't Lose Them"}

# PostPilot CTA (UTM-tracked). Override with --cta-url if white-labeling.
CTA_URL = ("https://www.postpilot.com/meet?utm_source=klaviyo-scoring-skill"
           "&utm_medium=html-report&utm_campaign=customer-scoring-audit")
CTA_HEADLINE = "Want PostPilot to run this winback for you?"
CTA_SUB = "We reach the At Risk and Can't Lose Them customers email can't — and we'll set the whole program up for you. Most brands are live within a week."
CTA_LABEL = "Book a strategy call"


def _money(x):
    return "${:,.0f}".format(x or 0)


TREATMENT = {
    "Champions": "Recognize, don't discount. VIP and early-access drops, gifts, the occasional handwritten thank-you. Protect this segment.",
    "Loyal Customers": "Keep them buying on cadence — replenishment reminders timed to their order interval, gentle cross-sell. Don't over-mail; they're your most email-engaged.",
    "Potential Loyalists": "Push them toward the 3rd order — post-purchase nurture, a second-order incentive, brand storytelling. The next 30-60 days decide if they become Loyal.",
    "New Customers": "Win the second purchase. A well-timed day 30-45 incentive (postcard or email) sharply raises conversion to repeat.",
    "At Risk": "Direct-mail winback with a strong offer. Email has stopped working on them; an off-email channel is how you get reactivations here.",
    "Can't Lose Them": "Treat each like a sales lead, not a marketing impression — premium mailer, personalized offer, even a call if the list is small. Highest priority.",
    "Hibernating": "A periodic mass-winback test makes sense; an expensive bespoke program doesn't. Consider sunsetting email after 12+ months of silence.",
    "Lost": "One final low-cost reactivation test, then sunset to protect deliverability.",
}


def _insight_sentences(result):
    """2-4 plain-English takeaways generated from the actual numbers."""
    g = result["gap"]
    segs = result["segments"]
    out = []

    def cnt(n):
        return (segs.get(n) or {}).get("count", 0)

    def ltv(n):
        return (segs.get(n) or {}).get("avg_clv", 0)

    ratio = g.get("gap_ratio_top_decile_vs_median")
    tdp = g.get("top_decile_pct_of_revenue")
    if ratio:
        conc = "highly concentrated" if ratio >= 10 else ("moderately concentrated" if ratio >= 6 else "relatively even")
        out.append(f"Your top 10% of customers are worth {ratio}x the median and drive {tdp}% of revenue — a {conc} customer base.")

    champ, cl = cnt("Champions"), cnt("Can't Lose Them")
    cl_ltv = ltv("Can't Lose Them")
    if cl and champ and cl >= 0.5 * champ:
        recover = max(int(cl * 0.10), 1)
        out.append(
            f"You have {cl:,} customers in Can't Lose Them — former high-value buyers who have gone cold — against {champ:,} active Champions. "
            f"Recovering even 10% (~{recover:,} at {_money(cl_ltv)} avg LTV) is worth roughly {_money(recover * cl_ltv)}."
        )

    ar = cnt("At Risk")
    if ar and result.get("buyers_scored") and ar >= 0.15 * result["buyers_scored"]:
        out.append(f"At Risk is one of your largest segments ({ar:,} customers) — a meaningful slice of the base is in slow-motion churn.")

    clicks = [(segs.get(n) or {}).get("click_rate_90d") for n in ("Can't Lose Them", "At Risk")]
    onsite = [(segs.get(n) or {}).get("onsite_rate_90d") for n in ("Can't Lose Them", "At Risk")]
    clicks = [c for c in clicks if c is not None]
    onsite = [o for o in onsite if o is not None]
    if clicks and onsite:
        avg_click = sum(clicks) / len(clicks)
        avg_onsite = sum(onsite) / len(onsite)
        out.append(
            f"Your highest-value lapsed segments click email ~{avg_click:.0f}% and visit the site ~{avg_onsite:.0f}% "
            "of the time in the last 90 days — email has effectively stopped reaching the customers worth the most. "
            "(Open rate looks higher, but Apple Mail Privacy Protection inflates it, so clicks and on-site are the real read.)"
        )
    return out[:4]


def _relax(nodes, x0, y0, x1, y1, passes=90, pad=4):
    """Nudge overlapping bubbles apart (simple pairwise repulsion), keeping them
    in bounds. Each node is a dict with cx, cy, r. Positions are approximate after
    this — bubbles stay near their true coordinate but stop stacking."""
    import math
    # break exact ties: coincident points have no repulsion direction, so fan
    # them out by a tiny golden-angle offset before relaxing.
    for idx, n in enumerate(nodes):
        n["cx"] += math.cos(idx * 2.399963) * 0.6
        n["cy"] += math.sin(idx * 2.399963) * 0.6
    for _ in range(passes):
        for i in range(len(nodes)):
            a = nodes[i]
            for j in range(i + 1, len(nodes)):
                b = nodes[j]
                dx = b["cx"] - a["cx"]
                dy = b["cy"] - a["cy"]
                dist = (dx * dx + dy * dy) ** 0.5 or 0.01
                mind = a["r"] + b["r"] + pad
                if dist < mind:
                    push = (mind - dist) / 2.0
                    ux, uy = dx / dist, dy / dist
                    a["cx"] -= ux * push
                    a["cy"] -= uy * push
                    b["cx"] += ux * push
                    b["cy"] += uy * push
        for nn in nodes:
            nn["cx"] = min(max(nn["cx"], x0 + nn["r"]), x1 - nn["r"])
            nn["cy"] = min(max(nn["cy"], y0 + nn["r"]), y1 - nn["r"])
    return nodes


def _svg_lorenz(points, w=520, h=320):
    P = PALETTE
    pad = 44
    iw, ih = w - pad * 2, h - pad * 2

    def X(p):
        return pad + iw * p / 100.0

    def Y(p):
        return pad + ih * (1 - p / 100.0)

    grid = "".join(
        f'<line x1="{pad}" y1="{Y(g)}" x2="{w-pad}" y2="{Y(g)}" stroke="{P["ink"]}" stroke-opacity="0.12"/>'
        for g in (0, 25, 50, 75, 100)
    )
    equality = f'<line x1="{X(0)}" y1="{Y(0)}" x2="{X(100)}" y2="{Y(100)}" stroke="{P["muted"]}" stroke-dasharray="4 4"/>'
    path = " ".join(f"{X(pt['cust_pct'])},{Y(pt['rev_pct'])}" for pt in points)
    curve = f'<polyline points="{path}" fill="none" stroke="{P["blue"]}" stroke-width="3"/>'
    # highlight top-10% point
    p10 = next((pt for pt in points if pt["cust_pct"] == 10), None)
    dot = ""
    if p10:
        dot = (
            f'<circle cx="{X(10)}" cy="{Y(p10["rev_pct"])}" r="5" fill="{P["lime"]}" stroke="{P["ink"]}"/>'
            f'<text x="{X(10)+8}" y="{Y(p10["rev_pct"])-8}" font-size="12" fill="{P["ink"]}">'
            f'top 10% = {p10["rev_pct"]}% of revenue</text>'
        )
    labels = (
        f'<text x="{pad}" y="{h-12}" font-size="11" fill="{P["muted"]}">0% of customers</text>'
        f'<text x="{w-pad}" y="{h-12}" font-size="11" fill="{P["muted"]}" text-anchor="end">100%</text>'
        f'<text x="12" y="{pad+6}" font-size="11" fill="{P["muted"]}">100% rev</text>'
    )
    return f'<svg viewBox="0 0 {w} {h}" width="100%" role="img">{grid}{equality}{curve}{dot}{labels}</svg>'


def _svg_seg_revenue(segments, w=520, rowh=30):
    P = PALETTE
    rows = [(s, segments[s]) for s in SEG_ORDER if s in segments]
    rows.sort(key=lambda kv: -kv[1]["pct_of_revenue"])
    mx = max((v["pct_of_revenue"] for _, v in rows), default=1) or 1
    label_w, bar_w = 150, w - 150 - 60
    h = rowh * len(rows) + 10
    out = []
    for i, (name, v) in enumerate(rows):
        y = i * rowh + 8
        bw = bar_w * v["pct_of_revenue"] / mx
        color = P["lime"] if name in DM_SEGS else P["blue"]
        out.append(
            f'<text x="0" y="{y+15}" font-size="12" fill="{P["ink"]}">{name}</text>'
            f'<rect x="{label_w}" y="{y+4}" width="{bw:.1f}" height="16" rx="3" fill="{color}"/>'
            f'<text x="{label_w+bw+6:.1f}" y="{y+16}" font-size="11" fill="{P["muted"]}">{v["pct_of_revenue"]}%</text>'
        )
    return f'<svg viewBox="0 0 {w} {h}" width="100%" role="img">{"".join(out)}</svg>'


def _svg_pareto(top_pct, w=520, h=70):
    P = PALETTE
    rest = round(100 - top_pct, 1)
    tw = (w - 4) * top_pct / 100.0
    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" role="img">'
        f'<rect x="0" y="20" width="{tw:.1f}" height="28" rx="4" fill="{P["lime"]}"/>'
        f'<rect x="{tw+4:.1f}" y="20" width="{w-tw-4:.1f}" height="28" rx="4" fill="{P["lightblue"]}"/>'
        f'<text x="4" y="14" font-size="12" fill="{P["ink"]}">Top 10% of customers: {top_pct}% of revenue</text>'
        f'<text x="{w-4}" y="14" font-size="12" fill="{P["muted"]}" text-anchor="end">Other 90%: {rest}%</text>'
        f"</svg>"
    )


SEG_ABBR = {
    "Champions": "Champs", "Loyal Customers": "Loyal", "Potential Loyalists": "Potential",
    "New Customers": "New", "At Risk": "At Risk", "Can't Lose Them": "Can't Lose",
    "Hibernating": "Hibernating", "Lost": "Lost", "Other / Mid-tier": "Other",
}


def _svg_lifecycle(segments, w=520, h=380):
    """LifeCycle grid: x = Recency score, y = Value score (max of frequency & spend,
    matching how segments are classified), bubble area = segment revenue."""
    P = PALETTE
    padl, padr, padt, padb = 54, 24, 24, 44
    iw, ih = w - padl - padr, h - padt - padb
    import math

    def X(r):  # recency 1..5 -> left..right
        return padl + iw * (r - 1) / 4.0

    def Y(f):  # frequency 1..5 -> bottom..top
        return padt + ih * (1 - (f - 1) / 4.0)

    grid = ""
    for i in range(1, 6):
        grid += (
            f'<line x1="{X(i)}" y1="{padt}" x2="{X(i)}" y2="{padt+ih}" stroke="{P["ink"]}" stroke-opacity="0.08"/>'
            f'<line x1="{padl}" y1="{Y(i)}" x2="{padl+iw}" y2="{Y(i)}" stroke="{P["ink"]}" stroke-opacity="0.08"/>'
        )
    max_rev = max((v["sum_clv"] for v in segments.values()), default=1) or 1
    nodes = []
    for name, v in segments.items():
        if not v.get("avg_r") or not v.get("avg_v"):
            continue
        rad = 7 + 26 * math.sqrt(v["sum_clv"] / max_rev)
        nodes.append({
            "name": name, "r": rad, "color": P["lime"] if name in DM_SEGS else P["blue"],
            "tx": X(v["avg_r"]), "ty": Y(v["avg_v"]),
            "cx": X(v["avg_r"]), "cy": Y(v["avg_v"]),
        })
    _relax(nodes, padl, padt, padl + iw, padt + ih)
    dots = "".join(
        f'<circle cx="{n["tx"]:.1f}" cy="{n["ty"]:.1f}" r="1.6" fill="{P["ink"]}" fill-opacity="0.35"/>'
        f'<line x1="{n["tx"]:.1f}" y1="{n["ty"]:.1f}" x2="{n["cx"]:.1f}" y2="{n["cy"]:.1f}" stroke="{P["ink"]}" stroke-opacity="0.15"/>'
        for n in nodes
    )
    bubbles = dots
    for n in sorted(nodes, key=lambda d: -d["r"]):  # largest first, labels on top
        bubbles += (
            f'<circle cx="{n["cx"]:.1f}" cy="{n["cy"]:.1f}" r="{n["r"]:.1f}" fill="{n["color"]}" fill-opacity="0.55" stroke="{P["ink"]}" stroke-opacity="0.35"/>'
            f'<text x="{n["cx"]:.1f}" y="{n["cy"]+3:.1f}" font-size="10" text-anchor="middle" fill="{P["ink"]}">{SEG_ABBR.get(n["name"], n["name"])}</text>'
        )
    axes = (
        f'<text x="{padl}" y="{h-14}" font-size="11" fill="{P["muted"]}">lapsed</text>'
        f'<text x="{padl+iw}" y="{h-14}" font-size="11" fill="{P["muted"]}" text-anchor="end">recent</text>'
        f'<text x="{(padl+iw/2):.0f}" y="{h-2}" font-size="11" fill="{P["muted"]}" text-anchor="middle">Recency &rarr;</text>'
        f'<text x="14" y="{padt+8}" font-size="11" fill="{P["muted"]}">high</text>'
        f'<text x="14" y="{padt+ih}" font-size="11" fill="{P["muted"]}">low</text>'
        f'<text transform="translate(16,{(padt+ih/2):.0f}) rotate(-90)" font-size="11" fill="{P["muted"]}" text-anchor="middle">Value (freq or spend) &rarr;</text>'
    )
    return f'<svg viewBox="0 0 {w} {h}" width="100%" role="img">{grid}{bubbles}{axes}</svg>'


def _svg_engagement_grid(segments, w=520, h=380):
    """Engagement grid: x = 90d email open rate, y = 90d on-site rate, bubble area = revenue."""
    P = PALETTE
    import math
    padl, padr, padt, padb = 54, 24, 24, 46
    iw, ih = w - padl - padr, h - padt - padb
    segs = [(n, v) for n, v in segments.items()
            if v.get("click_rate_90d") is not None and v.get("onsite_rate_90d") is not None]
    if not segs:
        return ""
    xmax = max(5.0, max(v["click_rate_90d"] for _, v in segs) * 1.15)
    ymax = max(5.0, max(v["onsite_rate_90d"] for _, v in segs) * 1.15)

    def X(p):
        return padl + iw * min(p, xmax) / xmax

    def Y(p):
        return padt + ih * (1 - min(p, ymax) / ymax)

    grid = ""
    for frac in (0.25, 0.5, 0.75, 1.0):
        grid += (
            f'<line x1="{padl+iw*frac}" y1="{padt}" x2="{padl+iw*frac}" y2="{padt+ih}" stroke="{P["ink"]}" stroke-opacity="0.07"/>'
            f'<line x1="{padl}" y1="{padt+ih*(1-frac)}" x2="{padl+iw}" y2="{padt+ih*(1-frac)}" stroke="{P["ink"]}" stroke-opacity="0.07"/>'
        )
    max_rev = max((v["sum_clv"] for _, v in segs), default=1) or 1
    nodes = []
    for name, v in segs:
        rad = 7 + 26 * math.sqrt(v["sum_clv"] / max_rev)
        nodes.append({
            "name": name, "r": rad, "color": P["lime"] if name in DM_SEGS else P["blue"],
            "tx": X(v["click_rate_90d"]), "ty": Y(v["onsite_rate_90d"]),
            "cx": X(v["click_rate_90d"]), "cy": Y(v["onsite_rate_90d"]),
        })
    _relax(nodes, padl, padt, padl + iw, padt + ih)
    dots = "".join(
        f'<circle cx="{n["tx"]:.1f}" cy="{n["ty"]:.1f}" r="1.6" fill="{P["ink"]}" fill-opacity="0.35"/>'
        f'<line x1="{n["tx"]:.1f}" y1="{n["ty"]:.1f}" x2="{n["cx"]:.1f}" y2="{n["cy"]:.1f}" stroke="{P["ink"]}" stroke-opacity="0.15"/>'
        for n in nodes
    )
    bubbles = ""
    for n in sorted(nodes, key=lambda d: -d["r"]):
        bubbles += (
            f'<circle cx="{n["cx"]:.1f}" cy="{n["cy"]:.1f}" r="{n["r"]:.1f}" fill="{n["color"]}" fill-opacity="0.55" stroke="{P["ink"]}" stroke-opacity="0.35"/>'
            f'<text x="{n["cx"]:.1f}" y="{n["cy"]+3:.1f}" font-size="10" text-anchor="middle" fill="{P["ink"]}">{SEG_ABBR.get(n["name"], n["name"])}</text>'
        )
    bubbles = dots + bubbles
    ann = (
        f'<text x="{padl+2}" y="{padt+14}" font-size="10" fill="{P["muted"]}">active on both</text>'
        f'<text x="{padl+2}" y="{padt+ih-6}" font-size="10" fill="{P["navy"]}">dark &rarr; reach by mail</text>'
    )
    axes = (
        f'<text x="{(padl+iw/2):.0f}" y="{h-4}" font-size="11" fill="{P["muted"]}" text-anchor="middle">Email click rate, last 90d &rarr;</text>'
        f'<text x="{padl}" y="{h-24}" font-size="10" fill="{P["muted"]}">0%</text>'
        f'<text x="{padl+iw}" y="{h-24}" font-size="10" fill="{P["muted"]}" text-anchor="end">{xmax:.0f}%</text>'
        f'<text transform="translate(16,{(padt+ih/2):.0f}) rotate(-90)" font-size="11" fill="{P["muted"]}" text-anchor="middle">On-site rate, last 90d &rarr;</text>'
    )
    return f'<svg viewBox="0 0 {w} {h}" width="100%" role="img">{grid}{bubbles}{ann}{axes}</svg>'


def _svg_forecast(cumulative, w=520, h=180):
    """12-month cumulative net line (dark-section styling: lime line)."""
    P = PALETTE
    pad = 40
    iw, ih = w - pad * 2, h - pad * 2
    mx = max(cumulative) if cumulative else 1
    mx = mx or 1
    n = len(cumulative)
    pts = []
    for i, v in enumerate(cumulative):
        x = pad + iw * i / max(n - 1, 1)
        y = pad + ih * (1 - v / mx)
        pts.append((x, y))
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"{pad},{pad+ih} " + line + f" {pad+iw},{pad+ih}"
    end = pts[-1]
    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" role="img">'
        f'<polygon points="{area}" fill="{P["lime"]}" fill-opacity="0.12"/>'
        f'<polyline points="{line}" fill="none" stroke="{P["lime"]}" stroke-width="3"/>'
        f'<circle cx="{end[0]:.1f}" cy="{end[1]:.1f}" r="5" fill="{P["lime"]}"/>'
        f'<text x="{pad}" y="{h-10}" font-size="11" fill="#c9cacf">month 1</text>'
        f'<text x="{pad+iw}" y="{h-10}" font-size="11" fill="#c9cacf" text-anchor="end">month 12</text>'
        f'<text x="{end[0]-6:.1f}" y="{end[1]-10:.1f}" font-size="12" fill="{P["lime"]}" text-anchor="end">{_money(mx)}</text>'
        f"</svg>"
    )


def _logo_img(which="light", width_px=180):
    """Return an <img> of the bundled PostPilot wordmark as an embedded data URI,
    or a text wordmark fallback if the asset isn't found next to the script."""
    import base64
    here = os.path.dirname(os.path.abspath(__file__))
    rels = [f"../assets/brand/logo-{which}.svg", f"assets/brand/logo-{which}.svg", f"logo-{which}.svg"]
    for rel in rels:
        p = os.path.normpath(os.path.join(here, rel))
        try:
            with open(p, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode("ascii")
            return f'<img alt="PostPilot" src="data:image/svg+xml;base64,{b64}" style="width:{width_px}px;height:auto"/>'
        except OSError:
            continue
    color = "#fff" if which == "light" else PALETTE["ink"]
    return f'<div class="wordmark" style="color:{color}">PostPilot</div>'


def render_html(account, result, cta_url=CTA_URL):
    P = PALETTE
    g = result["gap"]
    dm = result.get("direct_mail") or {}
    stamp = dt.date.today().isoformat()
    seg_rows = ""
    for name in SEG_ORDER:
        s = result["segments"].get(name)
        if not s:
            continue
        hi = ' style="background:rgba(208,245,130,0.25)"' if name in DM_SEGS else ""
        seg_rows += (
            f"<tr{hi}><td>{name}</td><td>{s['count']:,}</td><td>{s['pct_of_buyers']}%</td>"
            f"<td>{_money(s['avg_clv'])}</td><td>{s['avg_orders']}</td>"
            f"<td>{s['avg_days_since']}d</td><td>{s['pct_of_revenue']}%</td></tr>"
        )

    def dm_card(title, d):
        if not d:
            return ""
        return (
            f'<div class="dmcard"><h4>{title}</h4>'
            f"<p><b>{d['audience']:,}</b> customers &middot; {int(d['response_rate']*100)}% resp &middot; {_money(d['aov'])} AOV</p>"
            f"<p class='net'>Net {_money(d['net'])} &middot; {d['roas']}x ROAS</p></div>"
        )

    dm_block = ""
    if dm.get("cant_lose_them") or dm.get("at_risk"):
        ev = dm.get("evergreen")
        ev_block = ""
        if ev:
            ev_block = f"""
          <div class="split">
            <div><div class="l">One-time blitz — mail the current pool once</div><div class="v">{_money(dm.get('combined_one_time_net'))}<span> net</span></div></div>
            <div><div class="l">Evergreen flow — ~{ev['monthly_volume']:,}/mo as customers lapse</div><div class="v">{_money(ev['annualized_net'])}<span> net / yr</span></div></div>
          </div>
          <div class="l" style="margin-top:20px">12-month cumulative net from the evergreen flow</div>
          {_svg_forecast(ev['forecast_cumulative'])}"""
        a = dm["assumptions"]
        atbl = f"""
          <table class="atbl"><thead><tr><th>Input</th><th>Can't Lose Them</th><th>At Risk</th></tr></thead><tbody>
          <tr><td>Audience</td><td>{(dm.get('cant_lose_them') or {}).get('audience',0):,}</td><td>{(dm.get('at_risk') or {}).get('audience',0):,}</td></tr>
          <tr><td>Response rate</td><td>{int(a['cant_lose_response']*100)}%</td><td>{int(a['at_risk_response']*100)}%</td></tr>
          <tr><td>Segment AOV</td><td>{_money((dm.get('cant_lose_them') or {}).get('aov',0))}</td><td>{_money((dm.get('at_risk') or {}).get('aov',0))}</td></tr>
          <tr><td>Cost / piece</td><td>${a['cant_lose_cost_per_piece']:.2f}</td><td>${a['at_risk_cost_per_piece']:.2f}</td></tr>
          </tbody></table>"""
        dm_block = f"""
        <section class="dark">
          <div class="kicker">THE DIRECT-MAIL OPPORTUNITY</div>
          <h2>Reach the customers email can't.</h2>
          <p class="sub">At Risk + Can't Lose Them are defined by low recency — email has stopped working on them, but they haven't stopped existing.</p>
          <div class="dmgrid">{dm_card("Can't Lose Them (highest-value winback)", dm.get('cant_lose_them'))}{dm_card("At Risk (volume winback)", dm.get('at_risk'))}</div>
          {ev_block}
          <div class="l" style="margin-top:22px">Assumptions (every input shown — edit in the script)</div>
          {atbl}
          <p class="fine">{a['source']}. Adjust the response rates and costs in <code>klaviyo_rfm_audit.py</code> to match your own history.</p>
        </section>"""

    cta_block = f"""
        <section class="dark cta">
          <div class="logo">{_logo_img("light", 170)}</div>
          <h2>{CTA_HEADLINE}</h2>
          <p class="sub">{CTA_SUB}</p>
          <p><a class="btn" href="{cta_url}">{CTA_LABEL} &rarr;</a></p>
        </section>"""

    has_engagement = any("open_rate_90d" in (result["segments"].get(s) or {}) for s in result["segments"])

    # Engagement grid (bubble chart): open rate x on-site rate, bubble = revenue
    eng_grid_block = ""
    if has_engagement:
        eng_grid_block = (
            '<div class="card"><h3>Engagement grid — who is active?</h3>'
            '<p style="font-size:13px;color:var(--muted);margin-top:-4px">Each bubble is a segment, placed by 90-day email <b>click</b> rate (x) and on-site rate (y); bubble size is share of revenue. We use clicks and on-site — not opens — because opens are inflated by Apple Mail Privacy Protection. Big bubbles in the bottom-left are high-value customers who have gone quiet on your owned channels — the direct-mail target.</p>'
            f"{_svg_engagement_grid(result['segments'])}</div>"
        )

    # Engagement chart-table (page 3): only if engagement was measured
    eng_block = ""
    if has_engagement:
        max_ltv = max((v["avg_clv"] for v in result["segments"].values()), default=1) or 1
        erows = ""
        for name in SEG_ORDER:
            s = result["segments"].get(name)
            if not s:
                continue
            barw = 100 * s["avg_clv"] / max_ltv
            barcolor = P["lime"] if name in DM_SEGS else P["blue"]
            erows += (
                f"<tr><td>{name}</td>"
                f'<td style="width:34%"><span style="display:inline-block;height:10px;width:{barw:.0f}%;background:{barcolor};border-radius:2px;vertical-align:middle"></span> <span style="color:var(--muted)">{_money(s["avg_clv"])}</span></td>'
                f'<td>{s.get("open_rate_90d","-")}%</td><td>{s.get("click_rate_90d","-")}%</td><td>{s.get("onsite_rate_90d","-")}%</td></tr>'
            )
        eng_block = (
            '<div class="card"><h3>Engagement by segment (last '
            f'{result.get("engagement_days") or 90} days)</h3>'
            '<table><thead><tr><th>Segment</th><th>Avg LTV</th><th>Email open*</th><th>Click</th><th>Active on site</th></tr></thead>'
            f"<tbody>{erows}</tbody></table>"
            '<p style="font-size:12px;color:var(--muted);margin-top:10px">Share of each segment with an event in the window. <b>*Open rate is unreliable</b> — Apple Mail Privacy Protection auto-opens email, which is why even Lost/Hibernating customers show 30-40%+ "opens." Trust <b>click and on-site</b> as the real engagement signals. Low click/on-site in a high-LTV segment (At Risk, Can\'t Lose Them) is the direct-mail signal.</p></div>'
        )

    sample_banner = ""
    if result.get("sampled") and result.get("sample_note"):
        sample_banner = f'<div class="samplebanner">Sampled read-only run · {result["sample_note"]}</div>'

    # "What this means" callout (auto-generated from the numbers)
    insight_block = ""
    sentences = _insight_sentences(result)
    if sentences:
        insight_block = '<div class="card insight"><h3>What this means</h3>' + "".join(f"<p>{s}</p>" for s in sentences) + "</div>"

    # "What to do" per-segment actions
    arows = ""
    for name in SEG_ORDER:
        if name not in result["segments"] or name not in TREATMENT:
            continue
        cls = ' class="dm"' if name in DM_SEGS else ""
        arows += f'<tr{cls}><td>{name}</td><td>{TREATMENT[name]}</td></tr>'
    actions_block = (
        '<div class="card"><h3>What to do with each segment</h3>'
        '<table class="act"><tbody>' + arows + "</tbody></table>"
        '<p style="font-size:12px;color:var(--muted);margin-top:10px">At Risk and Can\'t Lose Them (highlighted) are the segments built for direct mail — low recency means email has stopped reaching them.</p></div>'
    )

    fnote = ""
    if result.get("f_bracket_fallback_used"):
        fnote = "<li>Frequency used fixed brackets (1=F1, 2=F2, 3=F3, 4-5=F4, 6+=F5) because orders are one-order-dominated.</li>"

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Customer Scoring Audit — {account}</title>
<style>
:root{{--ink:{P['ink']};--paper:{P['paper']};--lime:{P['lime']};--blue:{P['blue']};--navy:{P['navy']};--muted:{P['muted']};}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);
font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;line-height:1.5}}
.wrap{{max-width:860px;margin:0 auto;padding:40px 24px 80px}}
.kicker{{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--navy);font-weight:600;margin-bottom:8px}}
h1{{font-size:34px;margin:.1em 0 .1em;letter-spacing:-.01em}}
h2{{font-size:24px;margin:.2em 0}} h3{{font-size:15px;text-transform:uppercase;letter-spacing:.06em;color:var(--navy);margin:0 0 12px}}
.meta{{color:var(--muted);font-size:13px;margin-bottom:28px}}
.card{{background:#fff;border:1px solid rgba(46,47,52,.08);border-radius:8px;padding:28px;margin:18px 0}}
.hero .num{{font-size:64px;font-weight:700;line-height:1;letter-spacing:-.02em}}
.hero .num b{{color:var(--navy)}}
.stats{{display:flex;gap:24px;flex-wrap:wrap;margin-top:18px}}
.stat{{flex:1;min-width:150px}} .stat .v{{font-size:24px;font-weight:700}} .stat .l{{font-size:12px;color:var(--muted)}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{text-align:right;padding:8px 6px;border-bottom:1px solid rgba(46,47,52,.08)}}
th:first-child,td:first-child{{text-align:left}} th{{color:var(--muted);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.05em}}
.dark{{background:var(--ink);color:#fff;border-radius:8px;padding:32px;margin:18px 0}}
.dark .kicker{{color:var(--lime)}} .dark .sub{{color:#c9cacf}}
.dmgrid{{display:flex;gap:16px;flex-wrap:wrap;margin:18px 0}}
.dmcard{{flex:1;min-width:220px;background:rgba(255,255,255,.06);border-radius:6px;padding:16px}}
.dmcard h4{{margin:0 0 8px;font-size:14px}} .dmcard p{{margin:4px 0;font-size:13px;color:#c9cacf}}
.net{{color:var(--lime);font-weight:600}} .net.big{{font-size:22px;margin-top:14px}}
.fine{{font-size:11px;color:var(--muted);margin-top:14px}}
.dark .l{{font-size:12px;color:#c9cacf;text-transform:uppercase;letter-spacing:.05em}}
.split{{display:flex;gap:24px;flex-wrap:wrap;margin-top:18px}}
.split>div{{flex:1;min-width:200px}} .split .v{{font-size:30px;font-weight:700;color:var(--lime);margin-top:4px}} .split .v span{{font-size:13px;color:#c9cacf;font-weight:400}}
.atbl{{margin-top:8px}} .atbl th,.atbl td{{border-bottom:1px solid rgba(255,255,255,.12);color:#e6e7ea}} .atbl th{{color:#9a9ba0}}
.cta{{text-align:center}} .cta .sub{{max-width:520px;margin:8px auto 0}} .cta .logo{{margin-bottom:10px}}
.brandbar{{margin-bottom:18px}} .wordmark{{font-size:22px;font-weight:800;letter-spacing:-.01em}}
.samplebanner{{background:#fff6d6;border:1px solid #e8d98a;border-radius:6px;padding:10px 14px;font-size:12px;color:#6b5d1a;margin:-8px 0 20px}}
.basis{{background:#fff;border:1px solid rgba(46,47,52,.08);border-radius:6px;padding:11px 14px;font-size:12px;color:var(--muted);margin:-6px 0 22px;line-height:1.55}}
a.btn{{display:inline-block;margin-top:16px;background:var(--lime);color:var(--ink);font-weight:700;text-decoration:none;padding:14px 28px;border-radius:6px}}
.insight{{border-left:4px solid var(--lime)}} .insight p{{margin:8px 0;font-size:15px}}
.act td{{text-align:left;vertical-align:top;font-size:13px}} .act td:first-child{{font-weight:600;white-space:nowrap;padding-right:16px}}
.act tr.dm td{{background:rgba(208,245,130,0.25)}}
.footer{{color:var(--muted);font-size:12px;margin-top:32px}} .footer h4{{color:var(--ink);font-size:13px;text-transform:uppercase;letter-spacing:.05em;margin:18px 0 6px}}
.footer ul{{margin:6px 0;padding-left:18px}}
.appendix{{margin-top:26px}}
@media print{{body{{background:#fff}} .card,.dark{{break-inside:avoid}} a.btn{{border:1px solid var(--ink)}}}}
</style></head><body><div class="wrap">
<div class="brandbar">{_logo_img("dark", 150)}</div>
<div class="kicker">Klaviyo Customer Scoring &middot; RFM</div>
<h1>Your customers are not all worth the same.</h1>
<div class="meta">{account} &middot; {result['buyers_scored']:,} buyers scored &middot; {stamp}</div>
{sample_banner}
<div class="basis"><b>Time basis:</b> lifetime value &mdash; revenue, LTV, "% of revenue" and order counts are <b>all-time</b> (each buyer's historic CLV and total orders). Recency is days since last purchase <b>as of today</b>. Engagement (open / click / on-site) is the <b>last 90 days</b>.</div>

<div class="card hero">
  <div class="num"><b>{g['gap_ratio_top_decile_vs_median']}x</b></div>
  <p>Your top 10% of customers are worth <b>{g['gap_ratio_top_decile_vs_median']}x</b> the median customer, and drive <b>{g['top_decile_pct_of_revenue']}%</b> of customer revenue.</p>
  <div class="stats">
    <div class="stat"><div class="v">{_money(g['top_decile_avg_clv'])}</div><div class="l">Top-decile avg LTV</div></div>
    <div class="stat"><div class="v">{_money(g['median_clv'])}</div><div class="l">Median LTV</div></div>
    <div class="stat"><div class="v">{_money(g['bottom_quartile_avg_clv'])}</div><div class="l">Bottom-quartile avg LTV</div></div>
    <div class="stat"><div class="v">{_money(result['overall_aov'])}</div><div class="l">Overall AOV</div></div>
  </div>
</div>
{insight_block}
<div class="card"><h3>Revenue concentration</h3>{_svg_pareto(g['top_decile_pct_of_revenue'])}
<div style="margin-top:18px">{_svg_lorenz(result['lorenz'])}</div></div>

<div class="card"><h3>Where your revenue lives, by segment</h3>
<p style="font-size:13px;color:var(--muted);margin-top:-4px">Each segment's share of <b>all-time customer lifetime value</b> (historic CLV) — not a trailing window. The <span style="display:inline-block;width:11px;height:11px;background:var(--lime);border-radius:2px;vertical-align:middle;border:1px solid rgba(46,47,52,.2)"></span> lime bars mark the direct-mail-addressable segments — At Risk and Can't Lose Them (high value, low recency) — a convention used throughout this report.</p>
{_svg_seg_revenue(result['segments'])}</div>

<div class="card"><h3>Lifecycle grid — where your customers sit</h3>
<p style="font-size:13px;color:var(--muted);margin-top:-4px">Each bubble is a segment, placed by recency (x) and value (y = frequency or spend, whichever is higher — the same basis segments are classified on); bubble size is the segment's share of revenue. So lapsed-but-valuable customers sit top-left. Lime = direct-mail-addressable (At Risk, Can't Lose Them).</p>
{_svg_lifecycle(result['segments'])}</div>
{eng_grid_block}
{eng_block}
{actions_block}
{dm_block}
{cta_block}
<div class="card appendix"><h3>Appendix — full segment detail</h3>
<table><thead><tr><th>Segment</th><th>Count</th><th>% buyers</th><th>Avg LTV</th><th>Avg orders</th><th>Avg days since</th><th>% revenue</th></tr></thead>
<tbody>{seg_rows}</tbody></table></div>
<div class="footer">
<h4>Method</h4>
RFM scoring on {result['buyers_scored']:,} buyers who purchased in the last {result['window_months']:g} months.
Recency from the Placed Order event stream; Monetary and frequency from Klaviyo predictive analytics (historic CLV / order count).
Engagement rates (when shown) are the share of each segment with an Opened Email, Clicked Email, or Active on Site event in the last {result.get('engagement_days') or 90} days. Quintile-based 1-5 scoring on each of R, F, M.
<ul>{fnote}</ul>
<h4>This audit is read-only</h4>
<ul><li>Does not create or modify segments, lists, profiles, flows, or campaigns.</li>
<li>Does not send any data outside your Klaviyo account.</li>
<li>Does not make ML predictions — RFM is deterministic, from data Klaviyo already tracks.</li></ul>
<h4>Run it yourself</h4>
Free and open. Install Claude (claude.com/download), create a read-only Klaviyo private API key, and run the Klaviyo Customer Scoring audit. Pairs with the Klaviyo Email Dormancy Audit (who stopped engaging with email) — this one answers who's worth reaching anyway.
<p style="margin-top:14px">Generated by the Klaviyo Customer Scoring audit &middot; {stamp}. Read-only; your data never leaves your Klaviyo account.</p>
</div>
</div></body></html>"""


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def write_outputs(account, result, out_dir, cta_url=CTA_URL):
    os.makedirs(out_dir, exist_ok=True)
    slug = "".join(c.lower() if c.isalnum() else "-" for c in account).strip("-") or "account"
    stamp = dt.date.today().isoformat()
    json_path = os.path.join(out_dir, f"rfm-{slug}-{stamp}.json")
    md_path = os.path.join(out_dir, f"rfm-{slug}-{stamp}.md")
    html_path = os.path.join(out_dir, f"rfm-{slug}-{stamp}.html")
    with open(json_path, "w") as fh:
        json.dump({"account": account, **result}, fh, indent=2)
    with open(html_path, "w") as fh:
        fh.write(render_html(account, result, cta_url=cta_url))

    g = result["gap"]
    order = [
        "Champions", "Loyal Customers", "Potential Loyalists", "New Customers",
        "At Risk", "Can't Lose Them", "Hibernating", "Lost", "Other / Mid-tier",
    ]
    lines = [
        f"# Klaviyo Customer Scoring Audit — {account}",
        f"*RFM analysis, {stamp}. Recency window: last {result['window_months']} months.*",
        "",
        "## The headline",
        f"Top 10% of customers are worth **{g['gap_ratio_top_decile_vs_median']}x** the median customer.",
        f"They drive **{g['top_decile_pct_of_revenue']}%** of customer revenue.",
        f"Top decile vs. bottom quartile: **{g['gap_ratio_top_decile_vs_bottom_q']}x** per customer.",
        "",
        f"Buyers scored: **{result['buyers_scored']:,}**  |  Overall AOV: **${result['overall_aov']}**",
        "",
        "## Segments",
        "",
        "| Segment | Count | % buyers | Avg LTV | Avg orders | Avg days since | % revenue |",
        "|---|---|---|---|---|---|---|",
    ]
    for seg in order:
        s = result["segments"].get(seg)
        if not s:
            continue
        lines.append(
            f"| {seg} | {s['count']:,} | {s['pct_of_buyers']}% | ${s['avg_clv']:,} | "
            f"{s['avg_orders']} | {s['avg_days_since']}d | {s['pct_of_revenue']}% |"
        )
    if result["f_bracket_fallback_used"]:
        lines += ["", "*Frequency used fixed brackets (1=F1, 2=F2, 3=F3, 4-5=F4, 6+=F5) because the order distribution is one-order-dominated.*"]
    lines += ["", f"Self-contained report: `{os.path.basename(html_path)}` &middot; data: `{os.path.basename(json_path)}`", ""]
    with open(md_path, "w") as fh:
        fh.write("\n".join(lines))
    return json_path, md_path, html_path


def print_console(account, result):
    g = result["gap"]
    print("\n" + "=" * 70)
    print(f"  {account} — RFM Customer Scoring")
    print("=" * 70)
    print(f"  Buyers scored:            {result['buyers_scored']:,}")
    print(f"  Top-decile vs median LTV: {g['gap_ratio_top_decile_vs_median']}x")
    print(f"  Top 10% share of revenue: {g['top_decile_pct_of_revenue']}%")
    print(f"  Overall AOV:              ${result['overall_aov']}")
    print("-" * 70)
    print(f"  {'Segment':<22}{'Count':>10}{'% buyers':>10}{'Avg LTV':>12}{'% rev':>8}")
    for seg, s in sorted(result["segments"].items(), key=lambda kv: -kv[1]["sum_clv"]):
        print(f"  {seg:<22}{s['count']:>10,}{s['pct_of_buyers']:>9}%${s['avg_clv']:>10,}{s['pct_of_revenue']:>7}%")
    print("=" * 70 + "\n")


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Portable Klaviyo RFM customer-scoring audit.")
    ap.add_argument("--site", help="Label to save/reuse an API key across runs (e.g. 'overtone').")
    ap.add_argument("--api-key", help="Klaviyo private API key (overrides env/saved).")
    ap.add_argument("--metric-id", help="Placed Order metric ID (skips auto-detection).")
    ap.add_argument("--window-months", type=float, default=36, help="Recency window in months (default 36).")
    ap.add_argument("--mode", choices=["full", "sampled"], default="full",
                    help="'full' = exact event walk (slow, needs a long-lived shell). 'sampled' = read-only representative sample with true per-buyer recency, checkpointed (works in short command windows).")
    ap.add_argument("--sample", type=int, default=0, help="[full mode] Enrich only N random buyers (0 = all).")
    ap.add_argument("--sample-size", type=int, default=0, help="[sampled mode] Target buyers to sample. 0 = auto (census if the base is small, ~1500 otherwise). Override with a number to force a size.")
    ap.add_argument("--recency-workers", type=int, default=8, help="[sampled mode] Parallel recency lookups (default 8).")
    ap.add_argument("--no-engagement", action="store_true", help="Skip the email/on-site engagement pass (faster on heavy email senders).")
    ap.add_argument("--engagement-days", type=int, default=90, help="Engagement lookback window in days (default 90).")
    ap.add_argument("--cta-url", default=CTA_URL, help="Override the CTA link (for white-labeling).")
    ap.add_argument("--revision", default=DEFAULT_REVISION, help=f"Klaviyo API revision (default {DEFAULT_REVISION}).")
    ap.add_argument("--out", default="./rfm-output", help="Output directory (default ./rfm-output).")
    args = ap.parse_args()

    key = resolve_api_key(args)

    print("\n  Validating key and reading account...", file=sys.stderr)
    account = get_account_name(key, args.revision)
    print(f"  Account: {account}", file=sys.stderr)

    metric_id = args.metric_id
    if not metric_id:
        metric_id, mname = find_placed_order_metric(key, args.revision)
        print(f"  Using metric: {mname} ({metric_id})", file=sys.stderr)

    t0 = time.time()
    if args.mode == "sampled":
        result = run_audit_sampled(key, args.revision, metric_id, args.out, args.sample_size, args.window_months,
                                    args.recency_workers, engagement=not args.no_engagement, engagement_days=args.engagement_days)
    else:
        result = run_audit(
            key, args.revision, metric_id, args.window_months, args.sample,
            engagement=not args.no_engagement, engagement_days=args.engagement_days,
        )
    elapsed = time.time() - t0

    json_path, md_path, html_path = write_outputs(account, result, args.out, cta_url=args.cta_url)
    print_console(account, result)
    print(f"  Done in {elapsed/60:.1f} min.", file=sys.stderr)
    print(f"  Wrote:\n    {html_path}  <- open this (self-contained report)\n    {json_path}\n    {md_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
