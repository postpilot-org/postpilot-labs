# Klaviyo Customer Scoring Audit

A free Claude plugin that scores every customer in a Klaviyo account on Recency, Frequency, and Monetary value — then delivers a self-contained HTML report showing exactly how much more your best customers are worth and how each segment should be treated. It runs as a small local script driven by a read-only Klaviyo API key, so it works on any Klaviyo account and your data never leaves your machine.

## What you get

Run the plugin (or the bundled script directly). In minutes you get a single **self-contained HTML report** — opens in any browser, no install, "Save as PDF" if you want a PDF — with:

1. **The gap** — the ratio between your top-decile and median customer, plus the top 10%'s share of revenue.
2. **Revenue concentration** — a Pareto split (top 10% vs. everyone else) and a full Lorenz curve of cumulative revenue by customer percentile, with the top-10% point highlighted.
3. **Lifecycle grid** — a Recency-by-Frequency bubble chart, one bubble per segment sized by its share of revenue, with the direct-mail-addressable segments in lime.
4. **Where your revenue lives** — segments ranked by share of revenue, with At Risk and Can't Lose Them called out.
5. **Engagement grid — who is active?** — a bubble chart plotting each segment by 90-day email open rate (x) and on-site rate (y), bubbles sized by revenue. Big bubbles in the bottom-left are high-value segments gone quiet on your owned channels.
6. **Engagement by segment** — email open, click, and Active-on-Site rates (last 90 days) per segment in table form.
6. **Your customer base, by segment** — count, % of buyers, avg LTV, avg orders, avg days since last purchase, and % of revenue for all eight RFM tiers.
7. **The direct-mail opportunity** — a sized winback for At Risk + Can't Lose Them: one-off blitz, evergreen monthly flow, a 12-month cumulative forecast, and every assumption shown.
8. **Book a call** — a UTM-tracked CTA to talk to PostPilot about running the winback.

Alongside the HTML the script also writes a JSON data file (feeds an optional live Cowork artifact) and a markdown summary.

## The framework

Every customer is scored 1–5 on three dimensions of the classic Recency / Frequency / Monetary framework:

- **Recency** — days since their last purchase. Lower is better.
- **Frequency** — total orders all-time. Higher is better.
- **Monetary** — total historic spend. Higher is better.

The (R, F, M) triplet lands each customer in one of eight named segments:

- **Champions** — recent, frequent, high-spend. Your VIPs.
- **Loyal Customers** — frequent, high-spend, mid-recency. The reliable middle of your VIP tier.
- **Potential Loyalists** — recent, mid-frequency. Trending toward Champion if you nurture them.
- **New Customers** — recent first-time buyers. The pipeline.
- **At Risk** — used to buy a lot, going quiet. The direct mail winback target.
- **Can't Lose Them** — your highest-LTV customers who have gone fully cold. The most painful segment.
- **Hibernating** — drifted off, never deeply engaged.
- **Lost** — long gone; worth one final test, then sunset.

For each segment the report shows: count, % of buyers, avg LTV, avg order frequency, days since last purchase, email open rate, click rate 90d, on-site rate 90d — and a specific treatment recommendation.

## The aha moment

In a typical DTC customer file, the top 10% of customers are ~5x more valuable than the median customer, drive around 30% of total revenue, and are 8x more valuable than the bottom quartile. Most marketing programs treat them the same as everyone else. The report makes that gap visible in one page and sizes the revenue you're leaving on the table.

## How to install and run

1. Install Claude desktop or Claude Code — [claude.com/download](https://claude.com/download)
2. Install this plugin — open the `.plugin` file in Claude
3. Say "score my customers" in any Claude conversation
4. The first run walks you through creating a **read-only Klaviyo private API key** (Settings → API keys). Paste it once; name the site and it's saved for reuse.

You can also run the bundled script on its own, anywhere Python is installed (no `pip install` — standard library only):

```
python3 skills/klaviyo-customer-scoring/scripts/klaviyo_rfm_audit.py --site mystore --out ./rfm-output
```

Completes large accounts (100K+ buyers) in minutes. Output is a single self-contained HTML report plus JSON and markdown. The report uses the PostPilot palette (charcoal + lime, off-white cards) with inline SVG charts and a system font stack, so it renders identically on any machine with no external fonts or dependencies. To restyle it, edit `render_html()` in the script.

> Note: because the full audit paginates an entire order history and customer file, it runs as a local script with a Klaviyo API key rather than through the Klaviyo MCP connector — an MCP/chat connector routes every row through the model's context and can't carry a full-base run.

## Trigger phrases

The skill picks up naturally on:

- "score my customers"
- "run an RFM analysis on my Klaviyo"
- "how valuable are my best customers?"
- "segment my customer base"
- "who are my VIPs and who's at risk?"
- "what's the gap between my best and average customers?"

## What it does NOT do

- Does **not** create or modify segments, lists, profiles, flows, or campaigns. Read-only.
- Does **not** send any data outside your Klaviyo account.
- Does **not** make ML predictions. RFM is deterministic — every score comes from data Klaviyo already tracks.
- Does **not** require Klaviyo CDP or any paid Klaviyo add-on.

The skill is read-only by design so you can safely run it against a production Klaviyo on the first try.

## Pairs well with

The companion plugin **Klaviyo Email Dormancy Audit** answers a different question: *who has stopped engaging with email?* This plugin answers: *who's valuable enough to keep reaching?* Run both — dormancy tells you who's unreachable, scoring tells you who's worth reaching anyway.

## Credit

Built and given away free by [PostPilot](https://www.postpilot.com/meet?utm_source=klaviyo-scoring-skill&utm_medium=readme&utm_campaign=customer-scoring-audit) — direct mail for DTC brands. The platform benchmarks used in the winback ROI math (7.6% median response rate on winback sends, 13.7% top-quartile) come from thousands of brands and winback campaigns sent across the PostPilot platform.

If you find this useful, give it a thumbs-up on LinkedIn or send it to a marketer friend. No login required, no data leaves your Klaviyo account.

## Changelog

**1.4.0** — HTML-first. The audit now runs as a bundled, dependency-free local Python script driven by a read-only Klaviyo API key (portable across accounts; first run walks you through key setup), and outputs a self-contained HTML report with inline SVG charts (Pareto, Lorenz, RF lifecycle bubble grid, segment revenue, engagement-by-segment table, segment table, sized direct-mail opportunity with a 12-month forecast, and a UTM-tracked CTA). "Save as PDF" from any browser if a PDF is wanted. The headless-Chromium PDF path is removed. An optional live Cowork artifact renders the same report inline. Fixes: recency now sourced from the Placed Order event stream (the profile predictive block has no last-order date); deterministic segment precedence so cold high-value buyers land in Can't Lose Them / At Risk rather than Loyal. Engagement is measured by walking the opens/clicks/on-site streams and can be skipped with `--no-engagement` on heavy senders.
**1.1.0** — Redesigned 8-page PDF built to the PostPilot design system with a bundled deterministic template. (Superseded by the HTML report in 1.4.0.)
**1.0.0** — Initial release.

## License

MIT
