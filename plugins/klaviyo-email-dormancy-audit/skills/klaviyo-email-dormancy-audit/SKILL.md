---
name: klaviyo-email-dormancy-audit
description: Run a one-page health audit of a Klaviyo account that shows the marketer what % of their email subscribers and buyers are actually active (clicked an email in the last 90 days), how that's trended over 6 months, and the direct mail winback opportunity sized in dollars. Produces a self-contained, shareable HTML report (no PDF, no dependencies to install). The audit reads four pre-built segments named "PostPilot Audit · ...". If those segments don't exist yet, the skill switches into setup mode and walks the user through building them (a one-time 5-minute Klaviyo configuration). Use this whenever someone asks about list health, list decay, engagement rate, dormancy, subscriber quality, "how is my Klaviyo doing," "what % of my list is active," "are my buyers still engaged," "is my list dying," or asks for an audit / scorecard / health check of their email program. Trigger even when the user uses adjacent language like "dormant customers," "ghost subscribers," "winback candidates," or "how engaged is my list."
---

# Klaviyo Email Dormancy Audit

Produces a self-contained, shareable **HTML report** (opens in any browser; "Save as PDF" if a PDF is wanted) showing what percentage of a brand's email subscribers and buyers are actually active, the 6-month engagement trend, and the dollar-sized direct mail winback opportunity. No PDF toolchain, no reportlab/matplotlib, nothing to install — the bundled builder is pure Python standard library.

The audit reads four specific segments by exact name. If they all exist, the audit runs in roughly 10 seconds. If any are missing, the skill switches to setup mode and outputs the segment definitions the user needs to build in Klaviyo's UI (one-time, about 5 minutes).

"Active" means **clicked** an email in the last 90 days — a behavioral signal that is immune to Apple Mail Privacy Protection (opens are deliberately not used, since MPP auto-opens inflate them).

## The four required segments

The audit expects these exact names. The "PostPilot Audit · " prefix namespaces them so they don't conflict with the user's existing segments.

1. `PostPilot Audit · Email Subscribers`
2. `PostPilot Audit · Active Subscribers`
3. `PostPilot Audit · All Buyers`
4. `PostPilot Audit · Active Buyers`

## How to run

### Step 0: Pre-flight

Call `klaviyo_get_account_details`. If it fails, tell the user to connect Klaviyo as an MCP connector and stop. If it succeeds, capture the account name.

### Step 1: Discover (parallel)

Fire two calls in the same round:

- `klaviyo_get_segments` with `filter: any(name, ["PostPilot Audit · Email Subscribers", "PostPilot Audit · Active Subscribers", "PostPilot Audit · All Buyers", "PostPilot Audit · Active Buyers"])` and `fields: ["name"]`. This returns just the segments that exist among the four expected.
- `klaviyo_get_metrics`. We need the "Placed Order" metric ID for the AOV query later.

### Step 2: Branch on whether all 4 segments exist

**If all four are present:** proceed to Step 3 (run the audit).

**If any are missing:** switch to setup mode. Output the setup block from the end of this file verbatim, customized with which segments are missing (mark the ones already built with a checkmark and the ones to create with a circle). Tell the user to build the missing segments, then say "audit my Klaviyo" again. Stop here. Do not proceed.

### Step 3: Run the audit (parallel)

In a single round of parallel calls:

- `klaviyo_get_segment` × 4, one per segment, with `includeProfileCount: true`. This gives the four headline counts.
- `klaviyo_query_metric_aggregates` on Clicked Email, `measurements: ["unique"]`, monthly interval, last 6 months. This is the trend chart data. (Find the Clicked Email metric ID from Step 1's `get_metrics` response. It's a Klaviyo internal metric, always present.)
- **`klaviyo_query_metric_aggregates` on Placed Order, `measurements: ["sum_value", "count", "unique"]`, monthly interval, last 12 months.** This is REQUIRED for the opportunity sizing on page 1. From the returned data:
  - `total_revenue` = sum of monthly `sum_value` over 12 months
  - `total_orders` = sum of monthly `count` over 12 months
  - `aov = total_revenue / total_orders`

### Step 3.5: Compute annual_ltv — TTM is the default, sampled CLV is opt-in

The "revenue at risk" math depends on per-buyer lifetime value. Use Approach 1 (TTM-based) by default. It's defensible, transparent, and grounded in actual revenue data. Only fall back to other approaches when TTM can't be computed.

**Approach 1 (DEFAULT — TTM-based, from the metric data you already pulled):**

You already have the 12-month metric aggregate for Placed Order with `sum_value`, `count`, and `unique`. Compute:

- `total_revenue` = sum of monthly `sum_value` over 12 months
- Estimated annual unique buyers = `sum_of_monthly_unique × 0.5` (the 0.5 factor accounts for ~50% buyer overlap month-to-month, a typical ecommerce pattern where active buyers appear in 4–6 of 12 months on average)
- `annual_ltv` = `total_revenue / estimated_annual_unique_buyers`
- Set `ltv_source` = `"ttm"`

This is the default for a reason: it uses actual revenue data, the math is defensible to a CFO, and it doesn't get skewed by sample bias.

**Approach 2 (optional, opt-in only — sampled historic CLV):**

ONLY use this if the user explicitly asks for it, OR if the brand's industry has a known long purchase cycle (durable goods, furniture, appliances) where TTM is known to dramatically understate LTV. Sampled CLV has real biases:

- 100 profiles is a small sample with high variance
- Klaviyo's profile sort order can favor heavy spenders, skewing the mean upward
- Outliers (one $5K customer in the sample) move the average significantly

If you do use it: call `klaviyo_get_profiles` **filtered to the "PostPilot Audit · All Buyers" segment** (this is critical — without the segment filter the sample includes non-buyers and the math is wrong). Include the `predictive_analytics` field, page_size 100. Average `historic_clv` across the returned profiles, dropping nulls. Set `ltv_source` = `"sampled_clv"`.

Compare the sampled value to the TTM value. If they're within 20% of each other, prefer TTM (it's defensible). If sampled is much higher AND the industry justifies it (e.g., a furniture brand where TTM legitimately undercounts), use sampled.

**Approach 3 (fallback only — industry-aware multiplier):**

ONLY use this when TTM can't be computed (no Placed Order metric, no ecommerce integration, empty data). Set the multiplier based on `klaviyo_get_account_details.attributes.industry`:

- Food & Beverage, Supplements: `ltv_multiplier: 5.0`
- Beauty, Haircare, Personal Care: `ltv_multiplier: 3.0`
- Apparel, Accessories: `ltv_multiplier: 2.0`
- Home Goods, Furniture: `ltv_multiplier: 1.5`
- Consumer Electronics, Appliances (long cycle): `ltv_multiplier: 1.2`
- Subscription products: `ltv_multiplier: 4.0`
- Anything else: `ltv_multiplier: 2.0`

The script will compute `annual_ltv = aov × ltv_multiplier`. Set `ltv_source: "industry_default"`.

**Always set `ltv_source` in the JSON** so the script labels the caption accurately. Default to `"ttm"` unless you have a specific reason to use something else.

**Critical: never let the opportunity band render empty.**

The opportunity band is the most important section of the audit. If you don't pass a valid `aov` to the builder, the band loses its dollar figures. Two rules:

1. Always include `aov` in the JSON payload. If the Placed Order query succeeds, compute and pass the real number. If it fails (no ecommerce integration, query errored, returned empty), pass `aov: 50` as a conservative DTC default. The script will mark it with an asterisk and note the estimate.
2. If the trend query (Clicked Email monthly) returns empty, pass `trend_vals: []`. The script will hide the trend card cleanly and push the opportunity band up. Never pass empty cells inside `trend_vals` (no zeros, no nulls). Either real numbers or an empty array.

**Winback sizing (computed by the builder — you don't pass these):**

The HTML builder sizes the direct-mail opportunity on **PostPilot platform medians**: a one-time send at the **7.6% median** response rate and an evergreen flow at the **13.7% top-quartile** rate, at **$0.64/piece** (4x6). The sensitivity table shows annualized net across the platform percentiles (25th 4.1% · median 7.6% · 75th 13.7% · 90th 22.4%). These match the klaviyo-customer-scoring tool so both audits quote the same benchmarks. Cite them as "PostPilot platform medians across thousands of brands and winback campaigns" — never specific brand counts or campaign volumes.

### Step 4: Compose the audit data

Build a JSON object with the numeric fields. The builder renders the whole report deterministically from the data you pass, so you don't need to write any copy.

```json
{
  "account_name": "...",
  "run_date": "Month D, YYYY",
  "total_subs": <int from segment 1>,
  "active_subs": <int from segment 2>,
  "total_buyers": <int from segment 3 or null>,
  "active_buyers": <int from segment 4 or null>,
  "lapsed_buyers": <total_buyers - active_buyers, or null>,
  "aov": <float, computed from Placed Order TTM>,
  "annual_ltv": <float, computed via Step 3.5 — sampled CLV preferred, else TTM, else null>,
  "ltv_source": "sampled_clv" | "ttm" | "industry_default" | null,
  "ltv_multiplier": <float, only set when using industry_default approach>,
  "trend_months": ["Dec", "Jan", "Feb", "Mar", "Apr", "May"],
  "trend_vals": [12400, 10800, 11200, 12000, 12600, 12900]
}
```

Set `lapsed_buyers` to `total_buyers - active_buyers`. We use the dormant-buyer count (buyers who haven't engaged with email in 90 days) as the direct mail audience. That's a reasonable winback target.

The builder computes the rest: the active-percentage headline stats, the revenue-at-risk band (dormant buyers × annual LTV), and the direct-mail winback (one-time + evergreen + sensitivity) sized to the brand's actual `dormant_buyers` and `aov`.

### Step 5: Build the HTML report

Write the JSON to a temp file, then call the bundled builder (pure standard library — nothing to install):

```bash
python3 "${PLUGIN_DIR}/skills/klaviyo-email-dormancy-audit/scripts/build_audit_html.py" \
  --input /tmp/audit_data.json \
  --output /tmp/klaviyo_audit.html
```

Resolve `${PLUGIN_DIR}` from the location of this SKILL.md (builder lives at `scripts/build_audit_html.py` next to it). It embeds the PostPilot logo from `assets/` automatically and needs no fonts or PDF toolchain.

### Step 6: Present the HTML report

Copy the `.html` to the user's workspace with a filename like `<AccountName>_Klaviyo_Email_Dormancy_Audit.html`, and present it (in Cowork, via `present_files`). Tell them it opens in any browser and "Save as PDF" makes a PDF if they want one. Then give a one-paragraph summary in chat: the two headline percentages, the revenue-at-risk number, and one interpretive sentence. Don't restate the report. In Cowork you may also `create_artifact` a live version from the same JSON.

## Edge cases

- **No ecommerce integration (no Placed Order metric):** skip the buyer side. Set `total_buyers`, `active_buyers`, `aov`, `lapsed_buyers` to null. The report renders with only the subscriber-side stat (buyer gauge, revenue-at-risk, and winback sections are omitted).
- **Account younger than 6 months:** shorten the trend window. If less than 3 months of data, set `trend_months` and `trend_vals` to empty lists and the script will skip the trend chart.
- **Very small account (<10K subs):** percentages will be noisy. Add a one-line caveat in a takeaway.
- **Buyer count > subscriber count:** the user probably built their segments wrong. Surface this as a data quality note and ask them to verify the segment definitions.

## What this skill does NOT do

- Does not create, modify, or delete any Klaviyo segments, lists, profiles, flows, or campaigns. Read-only.
- Does not send any data outside the user's Klaviyo account.
- Does not make predictive forecasts. The revenue-at-risk and DM-opportunity numbers are directional, computed from conservative defaults (5% response rate, $0.64 per piece) and the account's actual AOV.

---

## Setup mode output (use this verbatim when segments are missing)

When Step 2 detects any of the four segments are missing, output the following block in chat. Replace `[MISSING SEGMENT NAMES]` with the actual missing ones, and adjust the checklist at the top to show which are already built (✓) versus which need to be created (○).

---

**Setup required.** This audit reads four specific segments from your Klaviyo account. You're missing some of them. The setup takes about 5 minutes and you only do it once.

Status:
- [STATUS] PostPilot Audit · Email Subscribers
- [STATUS] PostPilot Audit · Active Subscribers
- [STATUS] PostPilot Audit · All Buyers
- [STATUS] PostPilot Audit · Active Buyers

**Build the missing segments in Klaviyo:**

In Klaviyo, go to Audience → Segments → Create Segment. Build each missing segment with the exact name and conditions below. Use Klaviyo's segment builder UI for all of these (no JSON or API knowledge required).

**1. PostPilot Audit · Email Subscribers**
- Condition: If someone *can receive email marketing* equals *true*

**2. PostPilot Audit · Active Subscribers**
- Condition: If someone *can receive email marketing* equals *true*
- AND: If someone *has Clicked Email* at least once in the last 90 days

**3. PostPilot Audit · All Buyers**
- Condition: If someone *has Placed Order* at least once over all time

(Note: if your ecommerce platform is something other than Shopify, use the equivalent "Placed Order" metric for your platform.)

**4. PostPilot Audit · Active Buyers**
- Condition: If someone *has Placed Order* at least once over all time
- AND: If someone *has Clicked Email* at least once in the last 90 days

Once all four segments are built and have finished processing (Klaviyo usually takes 1-5 minutes to populate a new segment), come back and say "audit my Klaviyo" again. The audit will run in about 10 seconds.

---

Stop here in setup mode. Do not attempt to compute anything from partial data.
