# Klaviyo Email Dormancy Audit

A free Claude plugin that runs a one-page health audit of any Klaviyo account.

## What it does

Most marketers think their email list is twice as healthy as it actually is. This skill cuts through the illusion by showing:

- **% of email subscribers active** — clicked at least one email in the last 90 days
- **% of buyers active** — same definition, applied to anyone who's ever placed an order
- **6-month engagement trend** — is the active base keeping pace with list growth, or quietly shrinking?
- **Revenue at risk** — LTV of dormant buyers, currently unreachable via email
- **Sized direct mail winback opportunity** — net revenue per month, with platform-data-backed assumptions

The audit uses clicks rather than opens because Apple Mail Privacy Protection inflates open rates by 20–40% on most lists. Clicks are intentional and trustworthy.

## How to install

1. Install Claude desktop (Cowork mode) or Claude Code — claude.com/download
2. Connect your Klaviyo account as an MCP connector — settings → connectors → Klaviyo
3. Install this plugin — open the `.plugin` file in Claude
4. Run it by saying "audit my Klaviyo" in any Claude conversation

## How to run

Just ask. The skill triggers on phrases like:

- "audit my Klaviyo"
- "how is my email list doing?"
- "what % of my subscribers are active?"
- "are my buyers still engaged?"
- "find my dormant customers"

A typical run takes about 30 seconds and produces a markdown report plus a visual scorecard you can screenshot.

## What it does NOT do

- Does **not** create or modify segments, lists, profiles, flows, or campaigns. Read-only.
- Does **not** send any data outside your Klaviyo account.
- Does **not** make predictive forecasts. The revenue numbers are directional benchmarks, not model outputs.

These constraints matter because the skill is often run against a production Klaviyo account on a first-touch basis.

## How to customize

Edit `skills/klaviyo-email-dormancy-audit/SKILL.md` to change:

- Active window (default 90 days)
- Direct mail response rate assumption (default 7.6% — median across 847 brands)
- Send cost assumption (default $0.75/piece)
- Definition of "buyer" or "VIP"

## Credit

Built and given away free by [PostPilot](https://postpilot.com) — direct mail for DTC brands. The platform benchmarks used in the direct-mail-opportunity math (7.6% median response, percentile distribution) come from 5,123 winback campaigns and 38.4M postcards sent on the PostPilot platform.

If you find this useful, give it a thumbs-up on LinkedIn or send it to a marketer friend. No login required, no data leaves your Klaviyo account.

## License

MIT
