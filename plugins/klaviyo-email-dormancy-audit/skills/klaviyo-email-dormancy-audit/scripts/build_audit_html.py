#!/usr/bin/env python3
"""
Klaviyo Email Dormancy Audit — self-contained HTML report builder.

Reads a small JSON payload (the counts + AOV + trend the skill gathers through
the Klaviyo connector) and writes a single self-contained .html file: no PDF,
no reportlab/matplotlib, no fonts to install. Opens in any browser; "Save as
PDF" from the browser if a PDF is wanted.

Usage:
    python3 build_audit_html.py --input audit_data.json --output audit.html

"Active" everywhere means CLICKED an email in the last 90 days — a behavioral,
Apple-Mail-Privacy-Protection-immune signal. Opens are deliberately not used.
"""

import argparse
import base64
import json
import os
import sys

# --- PostPilot palette (matches klaviyo-customer-scoring) ---
INK = "#2E2F34"; PAPER = "#F3F0EC"; LIME = "#D0F582"
BLUE = "#6AB1F3"; NAVY = "#398BC7"; MUTED = "#8A8B90"

# --- Winback benchmarks: PostPilot platform medians ---
RATE_MEDIAN = 0.076    # median winback response
RATE_TOP = 0.137       # top-quartile (warm, automated/evergreen)
SEND_COST = 0.64       # 4x6 postcard
DEFAULT_AOV = 50.0
DEFAULT_LTV_MULT = 2.0


def _money(x):
    x = x or 0
    if abs(x) >= 1_000_000:
        return f"${x/1_000_000:.1f}M"
    if abs(x) >= 1_000:
        return f"${x/1000:.0f}K"
    return f"${x:,.0f}"


def derive(d):
    """Compute all report numbers from the raw inputs. Never leaves the
    opportunity section empty (AOV defaults to a conservative $50)."""
    d.setdefault("send_cost", SEND_COST)
    if not d.get("aov"):
        d["aov"] = DEFAULT_AOV
        d["aov_is_estimate"] = True
    else:
        d["aov_is_estimate"] = False

    ts, as_ = d.get("total_subs") or 0, d.get("active_subs") or 0
    d["active_sub_pct"] = round(100 * as_ / ts, 1) if ts else None
    tb, ab = d.get("total_buyers"), d.get("active_buyers")
    if tb and ab is not None:
        d["active_buyer_pct"] = round(100 * ab / tb, 1)
        d["dormant_buyers"] = tb - ab
    d.setdefault("dormant_buyers", None)
    if not d.get("lapsed_buyers"):
        d["lapsed_buyers"] = d.get("dormant_buyers")

    # annual LTV per buyer
    if d.get("annual_ltv"):
        d["estimated_ltv"] = d["annual_ltv"]
        d.setdefault("ltv_source", "ttm")
    else:
        d["estimated_ltv"] = d["aov"] * DEFAULT_LTV_MULT
        d.setdefault("ltv_source", "default")

    if d.get("dormant_buyers"):
        d["revenue_at_risk"] = d["dormant_buyers"] * d["estimated_ltv"]

    lb = d.get("lapsed_buyers")
    if lb:
        aov, cost = d["aov"], d["send_cost"]
        d["ot_react"] = lb * RATE_MEDIAN
        d["ot_rev"] = d["ot_react"] * aov
        d["ot_cost"] = lb * cost
        d["ot_net"] = d["ot_rev"] - d["ot_cost"]
        d["ot_roas"] = d["ot_rev"] / max(d["ot_cost"], 1)
        mo = lb / 12.0
        d["ev_month_net"] = mo * (RATE_TOP * aov - cost)
        d["ev_annual_net"] = d["ev_month_net"] * 12
        d["ev_roas"] = (RATE_TOP * aov) / max(cost, 1)
        d["sensitivity"] = [(r, mo * (r * aov - cost) * 12)
                            for r in (0.041, 0.076, 0.137, 0.224)]
    return d


def _logo_tag(script_dir, width=150):
    for rel in ("../assets/postpilot-logo-white.png", "assets/postpilot-logo-white.png"):
        p = os.path.normpath(os.path.join(script_dir, rel))
        try:
            with open(p, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode("ascii")
            return f'<img alt="PostPilot" src="data:image/png;base64,{b64}" style="width:{width}px;height:auto"/>'
        except OSError:
            continue
    return '<div style="font-size:22px;font-weight:800;color:#fff">PostPilot</div>'


def _svg_trend(months, vals, w=620, h=200):
    if not vals or not any(vals):
        return ""
    pad = 40
    iw, ih = w - pad * 2, h - pad * 2
    mx = max(vals) or 1
    n = len(vals)
    pts = [(pad + iw * i / max(n - 1, 1), pad + ih * (1 - v / mx)) for i, v in enumerate(vals)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{BLUE}"/>' for x, y in pts)
    labels = "".join(
        f'<text x="{pad + iw * i / max(n-1,1):.1f}" y="{h-14}" font-size="10" fill="{MUTED}" text-anchor="middle">{m}</text>'
        for i, m in enumerate(months)
    )
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" role="img">'
            f'<polyline points="{line}" fill="none" stroke="{BLUE}" stroke-width="3"/>{dots}{labels}'
            f'<text x="{pad}" y="{pad-8}" font-size="10" fill="{MUTED}">monthly unique email clickers</text></svg>')


def _gauge(label, pct, sub):
    if pct is None:
        return ""
    return (f'<div class="stat"><div class="v">{pct:.0f}%</div>'
            f'<div class="l">{label}</div><div class="s">{sub}</div></div>')


def render_html(d, script_dir):
    logo = _logo_tag(script_dir)
    trend = _svg_trend(d.get("trend_months") or [], d.get("trend_vals") or [])
    aov_note = ' <span class="ast">*estimated</span>' if d.get("aov_is_estimate") else ""

    # revenue-at-risk band
    rar = ""
    if d.get("revenue_at_risk"):
        ltv_word = "actual" if d.get("ltv_source") in ("ttm", "sampled_clv") else "estimated"
        rar = f"""
        <section class="dark">
          <div class="kicker">REVENUE AT RISK</div>
          <div class="num">{_money(d['revenue_at_risk'])}</div>
          <p class="sub">{d['dormant_buyers']:,} buyers have gone dormant on email (no click in 90 days). At ~{_money(d['estimated_ltv'])} {ltv_word} annual value each, that's the customer relationship value email can no longer reach.</p>
        </section>"""

    # winback opportunity
    wb = ""
    if d.get("lapsed_buyers"):
        rows = "".join(
            f'<tr><td>{int(r*1000)/10:.1f}% response</td><td>{_money(v)}/yr</td></tr>'
            for r, v in d["sensitivity"])
        wb = f"""
        <section class="dark">
          <div class="kicker">THE DIRECT-MAIL WINBACK</div>
          <h2>Reach dormant buyers where email can't.</h2>
          <p class="sub">Your {d['lapsed_buyers']:,} dormant buyers, reached by postcard. Sized on PostPilot platform medians across thousands of brands and winback campaigns.</p>
          <div class="split">
            <div><div class="l">One-time send &middot; {RATE_MEDIAN*100:.1f}% response</div><div class="vv">{_money(d['ot_net'])}<span> net &middot; {d['ot_roas']:.1f}x ROAS</span></div></div>
            <div><div class="l">Evergreen flow &middot; {RATE_TOP*100:.1f}% response</div><div class="vv">{_money(d['ev_annual_net'])}<span> net / yr &middot; {d['ev_roas']:.1f}x ROAS</span></div></div>
          </div>
          <div class="l" style="margin-top:20px">Annualized net at different response rates</div>
          <table class="sens"><tbody>{rows}</tbody></table>
          <p class="fine">Assumptions: response rates are PostPilot platform medians (25th 4.1% &middot; median 7.6% &middot; 75th 13.7% &middot; 90th 22.4%). Send cost ${d['send_cost']:.2f}/piece (4x6). AOV {_money(d['aov'])}{aov_note}.</p>
        </section>"""

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Email Dormancy Audit — {d['account_name']}</title>
<style>
*{{box-sizing:border-box}} body{{margin:0;background:{PAPER};color:{INK};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;line-height:1.5}}
.wrap{{max-width:860px;margin:0 auto;padding:0 0 80px}}
.top{{background:{INK};color:#fff;padding:32px 40px}}
.top .kick{{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:{LIME};font-weight:600;margin-top:14px}}
.top h1{{font-size:30px;margin:.15em 0 .1em}} .top .meta{{color:#c9cacf;font-size:13px}}
.body{{padding:24px 40px}}
.kicker{{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:{NAVY};font-weight:600;margin-bottom:8px}}
h2{{font-size:23px;margin:.2em 0}} h3{{font-size:15px;text-transform:uppercase;letter-spacing:.06em;color:{NAVY};margin:0 0 12px}}
.card{{background:#fff;border:1px solid rgba(46,47,52,.08);border-radius:8px;padding:26px;margin:16px 0}}
.stats{{display:flex;gap:20px;flex-wrap:wrap}}
.stat{{flex:1;min-width:200px;background:#fff;border:1px solid rgba(46,47,52,.08);border-radius:8px;padding:22px}}
.stat .v{{font-size:52px;font-weight:700;line-height:1;color:{NAVY}}}
.stat .l{{font-size:13px;font-weight:600;margin-top:6px}} .stat .s{{font-size:12px;color:{MUTED}}}
.dark{{background:{INK};color:#fff;border-radius:8px;padding:30px;margin:16px 40px}}
.dark .kicker{{color:{LIME}}} .dark .sub{{color:#c9cacf;font-size:14px}}
.dark .num{{font-size:56px;font-weight:700;color:{LIME};line-height:1;margin:6px 0}}
.split{{display:flex;gap:20px;flex-wrap:wrap;margin-top:16px}}
.split>div{{flex:1;min-width:210px}} .split .l{{font-size:12px;color:#c9cacf;text-transform:uppercase;letter-spacing:.05em}}
.split .vv{{font-size:26px;font-weight:700;color:{LIME};margin-top:4px}} .split .vv span{{font-size:12px;color:#c9cacf;font-weight:400}}
.sens{{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px;color:#e6e7ea}}
.sens td{{padding:6px 4px;border-bottom:1px solid rgba(255,255,255,.12);text-align:left}} .sens td:last-child{{text-align:right;font-weight:600}}
.fine{{font-size:11px;color:{MUTED};margin-top:12px}} .ast{{color:{MUTED}}}
.cta{{text-align:center}} .cta a{{display:inline-block;margin-top:14px;background:{LIME};color:{INK};font-weight:700;text-decoration:none;padding:13px 26px;border-radius:6px}}
.footer{{color:{MUTED};font-size:12px;padding:8px 40px}}
@media print{{.card,.dark{{break-inside:avoid}}}}
</style></head><body><div class="wrap">
<div class="top">{logo}<div class="kick">Klaviyo Email Dormancy Audit</div>
<h1>How much of your list is actually alive?</h1>
<div class="meta">{d['account_name']} &middot; {d.get('run_date','')}</div></div>

<div class="body">
<div class="stats">
{_gauge("of email subscribers are active", d.get("active_sub_pct"), "clicked an email in the last 90 days")}
{_gauge("of buyers are active", d.get("active_buyer_pct"), "clicked an email in the last 90 days")}
</div>
</div>

{f'<div class="card" style="margin:16px 40px"><h3>Engagement trend (6 months)</h3>{trend}</div>' if trend else ''}
{rar}
{wb}

<section class="dark cta" style="text-align:center">
  <div class="kicker">POWERED BY POSTPILOT</div>
  <h2>Want us to run the winback for you?</h2>
  <p class="sub">We build the dormant-buyer audience and ship the first postcard send. Most brands are live within a week.</p>
  <p><a href="https://www.postpilot.com/meet?utm_source=postpilot-labs&utm_medium=dormancy-audit&utm_campaign=lead-magnet">Book a strategy call &rarr;</a></p>
</section>

<div class="footer"><b>Method:</b> "Active" = clicked an email in the last 90 days — a behavioral signal that is immune to Apple Mail Privacy Protection (opens are not used, since MPP inflates them). Counts read from four "PostPilot Audit ·" segments; revenue and AOV from your Placed Order metric. Read-only — this audit never creates, modifies, or deletes anything in your Klaviyo account, and your data never leaves it.</div>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", "-i", help="JSON file (reads stdin if omitted).")
    ap.add_argument("--output", "-o", required=True, help="Output .html path.")
    args = ap.parse_args()
    data = json.load(open(args.input)) if args.input else json.load(sys.stdin)
    data = derive(data)
    html = render_html(data, os.path.dirname(os.path.abspath(__file__)))
    with open(args.output, "w") as fh:
        fh.write(html)
    print(f"HTML written to {args.output} ({os.path.getsize(args.output):,} bytes)")


if __name__ == "__main__":
    main()
