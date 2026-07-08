---
name: klaviyo-customer-scoring
description: Run an RFM-based customer scoring report on any Klaviyo account. Scores every customer on Recency, Frequency, and Monetary value, bins them into eight standard segments (Champions, Loyal, At Risk, Can't Lose Them, Hibernating, Lost, New, Potential Loyalists), and surfaces the value gap between the top decile and the average customer. Use whenever someone asks to "score my customers," "run an RFM analysis," "segment my customer base," "find my VIPs," "show me my best customers," "how much more valuable are my top customers," "who's at risk of churning," or any variation on customer-tiering. Also offer it proactively after the dormancy audit — it's the natural follow-up that answers "OK, who's actually worth fighting for?"
---

# Klaviyo Customer Scoring Audit (RFM)

## Runtime requirements

**This audit runs as a bundled local Python script (`scripts/klaviyo_rfm_audit.py`) driven by a Klaviyo private API key — NOT through the MCP connector.** It completes large accounts (100K+ buyers) in minutes. The script is pure standard library (no `pip install`).

Why not the MCP connector: a full-base RFM audit paginates the entire Placed Order history plus every buyer profile. Through an MCP/chat connector, every row flows through the model's context, which caps at ~60 rows per call and a total far below what a full account requires — a full walk is >100 MB and cannot complete, regardless of time. The script avoids this entirely by pulling and aggregating locally; only the finished report comes back.

What the script does: auto-detects the account and Placed Order metric, walks the event stream for true recency + frequency, enriches buyers with `historic_clv` (Monetary) and `historic_number_of_orders` in 100-ID batches, scores RFM into the eight segments, measures per-segment email + on-site engagement (Opened Email / Clicked Email / Active on Site in the last 90 days), and writes a self-contained HTML report plus a JSON data file and a markdown summary. See Step 1.

Note on runtime: scoring completes in minutes. The engagement pass is the slowest part on heavy email senders (it walks the opens/clicks/on-site event streams), and can be turned off with `--no-engagement` if speed matters more than the engagement table.

Do NOT paginate the Klaviyo profile list through the connector — that list is every subscriber (1M+ on a large brand, no server-side buyer filter), and it is the original cause of hours-long runs.

---

Most brands treat their customer file as one undifferentiated blob. This skill scores every customer on the classic Recency / Frequency / Monetary framework, bins them into the eight standard RFM segments, and surfaces the dollar gap between the top decile and the average customer. The output is a report a marketer can take to a CMO or board meeting.

## What you'll produce

The bundled script produces the first two automatically. The third is a Cowork-only extra.

1. **A self-contained HTML report** (the report of record) — one `.html` file written by the script, with the headline gap, revenue-concentration Pareto + Lorenz curve, the RF lifecycle bubble grid, the segment-revenue ranking, the engagement-by-segment table (email open / click / on-site), the full segment table, the sized direct-mail opportunity (one-off + evergreen + 12-month forecast + assumptions), and the UTM-tracked PostPilot "book a call" CTA — all inline SVG/CSS. It opens in any browser on any computer with **no dependencies and no install** — this is what makes the audit portable and shareable. Anyone who wants a PDF can use their browser's "Save as PDF". Present this file first.
2. **A markdown summary in chat** with the headline gap, the segment table, treatment recommendations, and the direct mail sizing (Step 7 structure). The script also writes a `.md`; expand it in chat with the treatment narrative.
3. **(Cowork only) A live artifact** — optionally call `create_artifact` to render the same report inline in Cowork from the script's JSON output. This is a convenience view for people inside Cowork; it is NOT the shareable deliverable (the HTML file is). Skip it outside Cowork.

Do NOT try to build a PDF with headless Chromium / Playwright / reportlab. The HTML report replaces the old PDF path; there is no Chromium dependency anymore.

## The framework

RFM is the oldest and most-defensible customer-scoring framework in retail. Three dimensions:

- **R (Recency)** — days since last purchase. Lower is better. Sourced from the **date of the buyer's most recent Placed Order event** (`get_events`, sorted `-datetime`). There is NO `last_order_date` field in the profile predictive block — do not use `expected_date_of_next_order − average_days_between_orders` as a stand-in; on live data that proxy is off by 90 days to 1.5 years and is null for one-time buyers.
- **F (Frequency)** — total number of orders all-time. Higher is better. Sourced from `predictive_analytics.historic_number_of_orders`.
- **M (Monetary)** — total historic spend (HCLV). Higher is better. Sourced from `predictive_analytics.historic_clv`.

Score each customer 1–5 on each dimension using **account-specific quintiles** — not absolute cutoffs. Quintile-based binning makes the report comparable across brands of any size or AOV.

Then bin the resulting (R, F, M) triplet into one of eight segments using the standard grid below. The grid is well-established (originally from Putler, widely used in ecommerce) and gives every customer a clear "what do I do with this person" label.

### The eight segments

| Segment | R | F | M | Plain-English description |
|---|---|---|---|---|
| **Champions** | 4-5 | 4-5 | 4-5 | Recent, frequent, high-spend. Your top customers. |
| **Loyal Customers** | 2-5 | 3-5 | 3-5 | High-frequency and high-spend but not necessarily the most recent. The reliable middle of your VIP tier. |
| **Potential Loyalists** | 3-5 | 1-3 | 1-3 | Bought recently a couple of times. Trending toward Champion if you nurture them right. |
| **New Customers** | 4-5 | 1 | 1-3 | Bought very recently for the first time. The pipeline. |
| **At Risk** | 2-3 | 2-5 | 2-5 | Used to buy often and spend a lot. Has gone quiet. Recoverable. |
| **Can't Lose Them** | 1-2 | 4-5 | 4-5 | Your highest-LTV customers who have gone fully cold. The most painful segment — losing one of these is losing a Champion. |
| **Hibernating** | 1-2 | 1-2 | 1-2 | Low recency, low frequency, low spend. Drifted off, but never deeply engaged. |
| **Lost** | 1 | 1 | 1 | Long gone, low historical value. Worth a final test, then sunset. |

For customers who don't cleanly fall into any of the above (which is rare with proper quintile binning), bucket them as "Other / Mid-tier" and exclude from the segment-specific narrative.

## How to run the audit

**The audit runs as a bundled local script, not through the MCP connector.** A full-base RFM audit means paginating the entire Placed Order history plus every buyer profile. Through an MCP/chat connector, every row flows through the model's context, which caps out almost immediately (a full walk is >100 MB of data and cannot complete). The bundled script pulls and crunches everything locally from a Klaviyo private API key — the same job finishes in minutes. **Do not attempt to reproduce the data pull with `klaviyo_get_profiles` / `klaviyo_get_events` MCP calls in-context; run the script.**

### Step 0: Ask for API access FIRST (before anything else)

**The very first thing to do when this skill runs is tell the user the audit needs a read-only Klaviyo API key, and ask them to provide it.** Do not start pulling data, locating files, or explaining the framework until access is settled. Lead with the speed benefit and the privacy reassurance, something like:

> "To speed this up, I'll need a read-only API key for your Klaviyo — it stays private here in your Claude, is only used to read your own account, never writes anything, and you can revoke it anytime. Here's how to get one:
>
> Klaviyo → Settings → API keys → **Create Private API Key** → **Custom scopes** → **Read** access to Accounts, Metrics, Events, and Profiles. Then paste it here.
>
> (Prefer the key never appear in chat? Set it as the `KLAVIYO_API_KEY` environment variable, or run the one-line script yourself — I'll give you the command.)"

Then confirm which account/store it's for, and **set time expectations**: tell the user this isn't instant — the script paginates the full order history, so on a real store it runs for **several minutes** (longer with the engagement pass on a heavy email sender). Say you'll run it in the background and come back with the report when it's done, so they're not left wondering. Something like: *"This takes a few minutes to run — it's pulling your full order history in the background. I'll share the report the moment it's ready."*

Only once you have the key (or the user has chosen to run it themselves) do you proceed to Step 1. If the user can't or won't provision a key, offer the degraded MCP-only fallback in Step 2.

**Running it in the background.** Because the run exceeds a single command window, launch it detached (e.g., `nohup ... &`) writing progress to a log file, then poll the log and report milestones ("walking the order stream… enriching buyers… scoring…") rather than blocking silently. Consider a fast first pass (`--no-engagement`, `--window-months 12`) to get the headline gap + segments quickly, then the full pass (engagement + 36-month window). Run the script with `python3 -u` so progress lines aren't buffered.

### Step 1: Run the script

The script is bundled at `scripts/klaviyo_rfm_audit.py` (relative to this SKILL.md). It is pure Python standard library — no `pip install` needed. Run it **from its `scripts/` directory** (so it can find the bundled logo in `../assets/brand/`).

**Two modes — default to `--mode sampled` in Cowork / short-command environments:**

- **`--mode sampled` (recommended, read-only, shareable):** draws a **volume-weighted** representative sample of buyers (stratified across signup history, weighted by each era's order volume so recent cohorts aren't under-represented), gets each buyer's **true last-purchase recency**, their **CLV + order count**, AND their **email/on-site engagement** (open / click / Active-on-Site in the last 90 days) via per-buyer read-only lookups, then **extrapolates every segment count and the direct-mail opportunity up to the full customer file** (total buyers ≈ all-time orders ÷ avg orders-per-buyer). **Auto-sizes** by default: censuses small accounts (≲4K buyers), samples ~1,500 otherwise (a proportion's precision is ~independent of base size, so 1,500 is enough whether the store has 10K or 5M buyers). All phases run in parallel and **checkpoint to disk**, so it resumes cleanly across short command windows. This is the mode to use for other marketers.
- **`--mode full` (exact, needs a long-lived shell):** walks the entire Placed Order event stream. Exact, but 20–40 min on a large store and won't complete where background processes get killed.

**Both modes must produce the complete report — including the engagement grid and the engagement-by-segment table.** If the engagement charts are missing from the output, engagement wasn't measured; do not ship the report without them unless the user passed `--no-engagement`.

Command (sampled, the normal case):
```
python3 klaviyo_rfm_audit.py --mode sampled --site <label> --out ./rfm-output
```
- `--sample-size` defaults to **0 = auto** (census if the base is small, ~1500 otherwise). Pass a number to force a size; larger mainly tightens the small high-value segments.
- `--site <label>` saves the key (chmod 600, in `~/.config/klaviyo-rfm/keys.json`) and reuses it. Different account = different label.
- Key resolution: `--api-key` → `KLAVIYO_API_KEY` env var → saved `--site` → interactive setup.
- `--no-engagement` skips the engagement pass (faster, but drops the engagement grid + table). `--recency-workers N` tunes parallelism (default 8).
- If Placed Order auto-detection fails, the script lists metrics; re-run with `--metric-id <ID>`.
- **Resuming:** if the command window ends mid-run, just run the exact same command again — it picks up from the last checkpoint.

The script auto-detects the account and the Placed Order metric, walks the event stream for true recency + frequency, enriches buyers with `historic_clv` (Monetary) in 100-ID batches, scores RFM into the eight segments, and writes two files to the output dir:
- `rfm-<account>-<date>.html` — the **self-contained report** (Step 8), and `rfm-<account>-<date>.json` — the **data contract** (also feeds the optional Cowork artifact).
- `rfm-<account>-<date>.md` — a markdown summary (headline gap + segment table).

If there is no ecommerce/Placed Order metric, the script exits with a message — recommend the dormancy audit instead (it works on email data alone).

### Step 2: (No manual data pull.)

The script did the data pull, scoring, and segmentation. Load the emitted `rfm-<account>-<date>.json` — it contains `buyers_scored`, `total_revenue`, `overall_aov`, the `gap` block (top-decile vs. median ratio, top-10% revenue share, etc.), and the per-segment stats (count, % of buyers, avg/median LTV, avg orders, avg days since, avg AOV, % of revenue). Use these numbers directly in Steps 5–8.

**MCP-only fallback (no API key available).** If the user cannot provision a private API key, you can approximate the report from existing Klaviyo segments via the connector: call `klaviyo_get_segments`, match common names ("VIP"/"Champions" → Champions, "Repeat Buyers" → Loyal, "First-Time Buyers" → New, "Lapsed"/"Churned" → At Risk, "Lost"/"Hibernating" → Lost/Hibernating), pull each count with `includeProfileCount: true`, and note in the methodology footer that segments came from the account's existing definitions rather than fresh RFM scoring. This is a degraded fallback — the script path is strongly preferred.

### Step 3: How the script scores (reference)

*The script performs Steps 3–4 automatically; this section documents the method so the output is explainable and tunable (edit `classify()` and the scorers in the script to change it).*

From the in-window buyer dataset (R from the event walk, F/M from enrichment), it computes quintile cutoffs (20th, 40th, 60th, 80th percentiles) for each of R, F, M.

- **R cutoffs**: percentile of days-since-last-order (from the event-stream last-order date). **Note the direction flip** — for Recency, *lower days-ago is better*, so score 5 = bottom 20% of days-ago, score 1 = top 20% of days-ago.
- **F cutoffs**: percentile of `historic_number_of_orders`. Score 5 = top 20%.
- **M cutoffs**: percentile of `historic_clv`. Score 5 = top 20%.

Edge case handled automatically: if the F distribution is heavily one-order-dominated (>50% of buyers with exactly 1 order, which is typical), the script switches to fixed F brackets (1 order = F1, 2 = F2, 3 = F3, 4-5 = F4, 6+ = F5) and flags it in the output (`f_bracket_fallback_used`). Surface that in the methodology note.

### Step 4: Score each customer and bin into segments (reference — done by the script)

For each buyer profile:

From the in-window buyer dataset assembled in Step 2 (R from the event walk, F/M from enrichment), compute the quintile cutoffs (20th, 40th, 60th, 80th percentiles) for each of R, F, M.

- **R cutoffs**: percentile of days-since-last-order (from the event-stream `last_order_date`). **Note the direction flip** — for Recency, *lower days-ago is better*, so score 5 = bottom 20% of days-ago, score 1 = top 20% of days-ago.
- **F cutoffs**: percentile of `historic_number_of_orders`. Score 5 = top 20%.
- **M cutoffs**: percentile of `historic_clv`. Score 5 = top 20%.

Edge case: if the F distribution is heavily skewed (e.g., 70% of buyers have exactly 1 order, which is typical), the quintile boundaries for F will collapse. In that case, use these fallback F-score brackets: 1 order = F1, 2 orders = F2, 3 orders = F3, 4-5 orders = F4, 6+ orders = F5. State this transparently in the report's methodology note.

### Step 4: Score each customer and bin into segments

For each buyer profile:
1. Compute R-score, F-score, M-score using the quintile cutoffs from Step 3.
2. Apply the segment grid from the framework section above to assign a segment label.
3. Tally the counts and aggregate stats per segment:
   - Count of customers
   - % of buyer base
   - Average historic_clv
   - Median historic_clv
   - Average historic_number_of_orders
   - Average days_since_last_order (from the event-stream last_order_date)
   - Average AOV (avg of avg_order_value across the segment)
   - Sum of historic_clv (the segment's contribution to total revenue)

### Step 5: Compute the headline gap

This is the most important number in the report.

Compute the **top-decile customer LTV** (90th percentile of historic_clv among buyers) and the **median customer LTV** (50th percentile). The ratio is the "gap":

```
gap_ratio = top_decile_LTV / median_LTV
```

In most ecommerce brands this lands somewhere in the 8x–15x range. A higher ratio means a more concentrated customer base (a few whales, many one-timers). A lower ratio means a flatter distribution (more uniformly engaged customers).

Also compute the **% of total revenue contributed by the top 10% of customers** — the classic Pareto cut. This is typically 40–60% of revenue.

Also compute the **top decile vs. bottom quartile** ratio, which is usually 30x–50x. This is the most dramatic number for the LinkedIn-share scorecard.

Also pull the account's trailing-12mo AOV from `klaviyo_query_metric_aggregates` on Placed Order — needed for the direct mail sizing. Always query Placed Order with a date range of the last 12 months — never the full account history — to keep latency predictable on large brands.

### Step 6: Size the direct mail opportunity for At Risk + Can't Lose Them

This is where the audit turns from a diagnosis into a business case. **At Risk** and **Can't Lose Them** are the two segments most directly addressable by direct mail, because both are defined by *low recency* — meaning email has, by definition, stopped working on them.

Apply these default benchmarks (state them explicitly in the report so the marketer can adjust):

- **Response rate**: 7.6% — the median across thousands of brands and winback campaigns in PostPilot's dataset. 25th percentile is 4.1%, 75th is 13.7%, 90th is 22.4%. Can't Lose Them and high-LTV At Risk customers typically respond in the 75th+ percentile (these are warm, high-intent winback audiences).
- **Send cost**: $0.64 per piece (4x6 postcard with an offer).
- **AOV**: use each segment's actual average AOV from Step 4, not a single account-wide number. Higher-value segments justify higher production cost per piece.

Compute three scenarios:

**Scenario 1 — Can't Lose Them winback (highest-priority sliver):**
- Audience: Can't Lose Them count
- Response rate: 13.7% (assume top-quartile, since this is the warmest possible winback audience)
- Revenue = audience × 13.7% × segment AOV
- Cost = audience × $0.64 (4x6 postcard)
- Net = revenue − cost
- ROAS = revenue / cost

**Scenario 2 — At Risk winback (the volume play):**
- Audience: At Risk count
- Response rate: 7.6% (median)
- Revenue = audience × 7.6% × segment AOV
- Cost = audience × $0.64 (4x6 postcard)
- Net = revenue − cost
- ROAS = revenue / cost

**Scenario 3 — Combined evergreen flow (steady state):**
- Monthly volume ≈ (At Risk + Can't Lose Them) / 12 — proxy for the natural lapse rate refilling the pool each month
- Annualized net = monthly volume × (blended response rate × blended AOV − blended cost) × 12

Be transparent about assumptions. The marketer should see every input.

### Step 7: Write the markdown report

Use this exact structure, filled in with the account's actual numbers. Keep it tight — the executive summary should fit on one screen.

```
# Klaviyo Customer Scoring Audit — {Account Name}
*RFM analysis run on {today's date}*

## The headline

Your top 10% of customers are worth **{gap_ratio}x** the median customer.
They contribute **{top_decile_revenue_pct}%** of your total customer revenue.

Your top decile vs. bottom quartile: **{top_to_bottom_ratio}x** more valuable per customer.

(RFM scoring: every customer scored 1-5 on Recency, Frequency, and Monetary value, using your account's own quintiles as cutoffs.)

## Your customer base, by segment

| Segment | Count | % of buyers | Avg LTV | Avg orders | Avg days since last buy | % of revenue |
|---|---|---|---|---|---|---|
| Champions | {n} | {pct}% | ${avg_ltv} | {avg_f} | {avg_r}d | {rev_pct}% |
| Loyal Customers | {n} | {pct}% | ${avg_ltv} | {avg_f} | {avg_r}d | {rev_pct}% |
| Potential Loyalists | {n} | {pct}% | ${avg_ltv} | {avg_f} | {avg_r}d | {rev_pct}% |
| New Customers | {n} | {pct}% | ${avg_ltv} | {avg_f} | {avg_r}d | {rev_pct}% |
| At Risk | {n} | {pct}% | ${avg_ltv} | {avg_f} | {avg_r}d | {rev_pct}% |
| Can't Lose Them | {n} | {pct}% | ${avg_ltv} | {avg_f} | {avg_r}d | {rev_pct}% |
| Hibernating | {n} | {pct}% | ${avg_ltv} | {avg_f} | {avg_r}d | {rev_pct}% |
| Lost | {n} | {pct}% | ${avg_ltv} | {avg_f} | {avg_r}d | {rev_pct}% |

## The gap, visually

Top 10% of customers: avg LTV **${top_decile_avg_ltv}**
Median customer: avg LTV **${median_ltv}**
Bottom 25%: avg LTV **${bottom_quartile_avg_ltv}**

Top 10% drive **{top_decile_revenue_pct}%** of all customer revenue. The other 90% drive the remaining **{100 - top_decile_revenue_pct}%**.

## How to treat each segment

**Champions ({champion_count})** — Your most valuable customers. They don't need a discount; they need to feel recognized. VIP loyalty mailers (premium format, foil/embossed), early-access drops, gifts on birthdays/anniversaries, hand-written thank-yous on a sample basis. Avoid discount-led messaging — it cheapens the relationship. Expect them to drive **{champion_revenue_pct}%** of revenue going forward; protect them.

**Loyal Customers ({loyal_count})** — Reliable repeat buyers. The job here is to keep them buying on cadence — replenishment reminders timed to their `average_days_between_orders`, cross-sell into adjacent categories, occasional loyalty rewards. This segment usually has the strongest email engagement, so don't over-mail them.

**Potential Loyalists ({pl_count})** — Recent customers with 2-3 orders. They're at the inflection point — your next 30-60 days of nurture determines whether they become Loyal Customers or churn. Heavier touch is justified: post-purchase flow, second-order incentive, brand-storytelling content.

**New Customers ({new_count})** — Recent first-time buyers. The pipeline. Focus on the second purchase. A well-timed second-order incentive (postcard or email) at day 30-45 dramatically improves the conversion-to-Loyal rate. AOV is typically lowest here; that's expected.

**At Risk ({atrisk_count})** — Used to be Champions or Loyal. Have gone quiet. **This is the highest-ROI segment to reach with off-email channels.** They've stopped opening email or unsubscribed; they haven't stopped existing. A direct mail winback with a strong offer typically pulls **7.6% response** at the median, **13.7%+** for the top quartile of this segment by historic LTV. Avg LTV in this group: **${atrisk_avg_ltv}** — meaning each reactivation is worth that future LTV continuation.

**Can't Lose Them ({cantlose_count})** — Your highest-LTV customers who have gone fully cold. Treat each one like a sales lead, not a marketing impression. Premium-format mailer, personalized offer, possibly even a phone call or hand-written letter if the segment is small enough. Avg LTV in this group: **${cantlose_avg_ltv}** — losing one is losing a Champion.

**Hibernating ({hib_count})** — Drifted off but were never Champions. A periodic mass-winback test makes sense; an expensive bespoke program probably doesn't. Consider sunsetting email sends after 12+ months of zero engagement to protect sender reputation.

**Lost ({lost_count})** — Long gone, low historical value. One final mass-reactivation test, then sunset.

## The direct mail opportunity

**At Risk + Can't Lose Them are the two segments built for direct mail.** Both are defined by low recency — meaning email has stopped working on them. Both retain meaningful historical value. Both convert when reached through an off-email channel.

**Can't Lose Them winback (highest priority):**
- Audience: {cantlose_count}
- Response rate: ~13.7% (top-quartile, warm winback audience)
- Revenue at ${cantlose_aov} avg AOV: ~${cantlose_revenue}
- Cost at $0.64/piece (4x6 postcard): ~${cantlose_cost}
- **Net: ~${cantlose_net} ({cantlose_roas}x ROAS)**

**At Risk winback (volume play):**
- Audience: {atrisk_count}
- Response rate: ~7.6% (median)
- Revenue at ${atrisk_aov} avg AOV: ~${atrisk_revenue}
- Cost at $0.64/piece (4x6 postcard): ~${atrisk_cost}
- **Net: ~${atrisk_net} ({atrisk_roas}x ROAS)**

**Combined evergreen flow (annualized steady state):**
- Monthly volume: ~{monthly_volume}
- **Annualized net: ~${annual_net}**

*Assumptions: 7.6% / 13.7% response rates (PostPilot platform medians across thousands of brands and winback campaigns in our dataset). $0.64 send cost (4x6 postcard). Segment-specific AOVs from your own data. Strong programs hit 22.4% on the warmest slivers. Adjust the rate in the skill if your historical performance is better or worse.*

## What this tells us

[2-4 sentences specific to the account. Examples of what to call out:
- If the gap_ratio is unusually high (>15x): "Your revenue is highly concentrated in a small Champion base. That's both an asset and a risk — losing a handful of Champions has outsized impact. Defending this segment should be the top retention priority."
- If Can't Lose Them count is large relative to Champions: "You have nearly as many former Champions sitting in Can't Lose Them as active Champions. Recovering even 10% of them is worth more than acquiring 100 new customers."
- If New Customer count is huge relative to repeat buyers: "Strong acquisition, weak retention conversion. The opportunity is in the New → Potential Loyalist → Loyal conversion flow, not more top-of-funnel.
- If At Risk is the largest segment: "Most of your customer base is in slow-motion churn. The cost of doing nothing is real — at $X avg LTV across At Risk, losing them silently leaves $Y on the table."]

## Recommended actions

1. [Specific to findings — usually a segment-specific direct mail program]
2. [Specific to findings — usually an email/flow change for a high-volume segment]
3. [Specific to findings — usually a measurement/instrumentation suggestion]

---

# Page 2: Share this audit with your team

This audit was generated by the **Klaviyo Customer Scoring Audit** skill — free, open, and installable in Claude.

## To install it yourself

1. Install Claude (Cowork or Claude Code) — claude.com/download
2. Connect your Klaviyo account as an MCP — settings → connectors → Klaviyo
3. Install this skill — [download link / install command]
4. Run it by saying "score my customers" in any Claude conversation

## To share with a colleague

Forward this report or send them the install link above. The skill is read-only — it never writes to or modifies a Klaviyo account.

## To customize

The skill is open. If you want to change segment definitions, quintile cutoffs, or response-rate assumptions, edit `SKILL.md` in the skill directory.
```

### Step 8: Present the HTML report (and, in Cowork, an optional artifact)

**The script already produced the report of record** — a single self-contained `rfm-<account>-<date>.html` in the output directory, containing: the headline gap; revenue-concentration Pareto + Lorenz curve (top-10% point highlighted); the RF lifecycle bubble grid (bubbles sized by segment revenue); an engagement grid ("who is active?" — 90-day email open rate on x, on-site rate on y, bubbles sized by revenue, so big low-engagement bubbles pop bottom-left); the segment-revenue ranking; the engagement-by-segment table (email open / click / on-site rates over the last 90 days, showing where high-LTV segments have gone quiet); the full segment table; the direct-mail opportunity (one-off blitz + evergreen flow + 12-month cumulative forecast + assumptions table); and the UTM-tracked PostPilot "book a call" CTA. It also includes a plain-English "What this means" callout (auto-generated from the numbers), a "What to do with each segment" actions table, a full-segment appendix table, and the PostPilot wordmark (header + CTA). Section order: hook → concentration → where revenue lives → lifecycle grid → engagement grid + engagement table → what to do → direct mail → CTA → appendix + methodology.

The wordmark loads from the bundled `assets/brand/` logos relative to the script; run the script from its `scripts/` directory (or leave the assets alongside it) so the logo resolves — otherwise it falls back to a text wordmark. It uses inline SVG and inline CSS with the PostPilot palette (charcoal `#2E2F34`, lime `#D0F582` reserved for direct-mail-addressable value, blue `#6AB1F3` default series, off-white `#F3F0EC`, navy `#398BC7`), a system font stack, and a print stylesheet. There is nothing to build and nothing to install.

Your job in this step:
1. **Present the HTML file** to the user (in Cowork, via `present_files`). This is the primary deliverable. Tell them it opens in any browser and that "Save as PDF" from the browser produces a PDF if they want one.
2. **Then** add the markdown summary in chat (Step 7 structure) with the treatment narrative.

**Optional — Cowork only:** you may also `create_artifact` a live inline version rendered from the script's JSON output, for people who want it in the Cowork panel. This is a convenience, not the deliverable, and should be skipped outside Cowork. Do not gate the HTML report on it.

**To restyle the report** (fonts, colors, which charts appear), edit `render_html()` and the `_svg_*` helpers in `scripts/klaviyo_rfm_audit.py`. To swap in the brand display font or wordmark, add them there — the report is intentionally dependency-free by default (no external fonts/CDN) so it stays portable across machines. Brand wordmark SVGs are available in `assets/` if you want to inline one.

Do NOT build a PDF via headless Chromium/Playwright or reportlab. That path is removed.

## Tone and framing

The point of this audit is to make the marketer realize that *treating all customers the same is the most expensive marketing mistake they're making.* Lead with the gap number and let it sit before going into segment-level prescriptions.

Don't lecture. The reader is a smart marketer who has heard of RFM. The value is that you actually ran it for them, on their data, with treatment recommendations they can act on Monday morning.

Avoid generic best-practice platitudes ("personalization is important"). Be specific to their segment counts and dollar values.

When the gap_ratio is healthy (<10x), say so plainly — flatter customer distributions are actually a sign of a well-run brand with strong retention across the board.

When findings are concerning (e.g., Can't Lose Them larger than Champions), say so plainly. Treat the marketer as a peer who can handle a hard number.

## When data is missing

- **No ecommerce integration**: abort with a clear message. Recommend the dormancy audit instead, which works on email data alone.
- **<500 buyers in the account**: quintile binning becomes statistically noisy. Run the analysis anyway but note in the report that segment sizes are small and recommendations should be directional.
- **Account with a long history**: keep the recency window at the default 12 months (or 36 for a definitive run); don't remove it "to be thorough." Buyers with no order in the window bin to Hibernating/Lost regardless, and the runtime cost of a wider window is high. Note in the report that the out-of-window tail is sized (all-time unique buyers minus in-window buyers), not enumerated.
- **Predictive analytics not available on profiles**: fall back to the segment-proxy approach in Step 2b.
- **No `historic_clv` field on profiles** (rare): compute M as `historic_number_of_orders × average_order_value` as a fallback, noting the substitution.
- **`average_order_value` missing**: pull account-wide trailing-12mo AOV via `klaviyo_query_metric_aggregates` and use that as a single fallback AOV across all segments.

## What this skill does NOT do

- Does not create or modify segments, lists, profiles, flows, or campaigns. Read-only.
- Does not send any data outside the user's Klaviyo account.
- Does not make ML predictions. RFM is deterministic — every customer's score derives directly from data Klaviyo already tracks.
- Does not require Klaviyo CDP or any paid Klaviyo add-on. The predictive analytics fields used (historic_clv, historic_number_of_orders, average_order_value) and the Placed Order event stream are standard across all Klaviyo plans for accounts with an ecommerce integration. (Recency comes from the event stream, not a predictive field — the profile block has no last-order-date.)

These constraints matter because this skill is often run against a production Klaviyo account on a first-touch basis. Respect that.

## Pairs with: Klaviyo Email Dormancy Audit

This skill and the dormancy audit are complementary, not duplicative:

- **Dormancy audit** answers: *who has stopped engaging with email?*
- **Customer scoring** answers: *who's valuable enough to keep reaching?*

The natural follow-up to a dormancy audit is to score the dormant cohort and identify which dormant customers are Can't Lose Them (highest-LTV, gone cold) vs. Hibernating (low-LTV, low engagement). The first group justifies direct mail; the second probably doesn't.

If both skills are installed, offer to chain them: run the dormancy audit, then propose the scoring audit as the next step.
