# PostPilot Labs

Free, read-only marketing audit tools for DTC and ecommerce brands, built by [PostPilot](https://www.postpilot.com). Point Claude at your own store's data and get a shareable report in minutes. Everything runs locally against your own account — **no data leaves your Klaviyo/store, and nothing is ever written to it.**

## Tools in this marketplace

- **klaviyo-customer-scoring** — an RFM customer-scoring audit for any Klaviyo account. Scores every customer on Recency, Frequency, and Monetary value; bins them into eight actionable segments (Champions, Loyal, Potential Loyalists, New, At Risk, Can't Lose Them, Hibernating, Lost); and delivers a self-contained HTML report with the value gap, revenue concentration, lifecycle and engagement bubble grids, and a sized direct-mail winback opportunity. Read-only; runs from your own read-only Klaviyo API key.

More free tools coming.

## How to install

1. Install Claude Code — [claude.com/download](https://claude.com/download)
2. **Add this marketplace:**
   ```
   /plugin marketplace add PostPilot-GTM/postpilot-labs
   ```
3. **Install the tool:**
   ```
   /plugin install klaviyo-customer-scoring@postpilot-labs
   ```
4. **Activate it:** `/reload-plugins`
5. **Run it** — say "score my customers." The first run walks you through creating a **read-only** Klaviyo private API key; it's only used to read your own account and you can revoke it anytime.

To update later: `/plugin marketplace update postpilot-labs`.

> Layout follows the standard `.claude-plugin/marketplace.json` spec, each tool in its own folder under `plugins/`. Command syntax verified against the [Claude Code plugin docs](https://docs.claude.com/en/docs/claude-code/discover-plugins).

## What these tools will and won't do

- **Read-only.** They never create, modify, or delete anything in your account.
- **Private.** Your data is pulled and crunched locally; only the finished report is produced. Your API key stays with you.
- **Portable.** Each report is a single self-contained HTML file — open it in any browser, or "Save as PDF."

## License

MIT.

---

Built and given away free by [PostPilot](https://www.postpilot.com/meet?utm_source=postpilot-labs&utm_medium=readme&utm_campaign=lead-magnet) — direct mail for DTC brands.
