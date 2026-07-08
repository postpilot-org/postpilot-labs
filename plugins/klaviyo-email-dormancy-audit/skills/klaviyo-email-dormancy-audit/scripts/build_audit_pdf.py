"""PostPilot Klaviyo Email Dormancy Audit — PDF generator.

Bundled with the klaviyo-email-dormancy-audit plugin. Called by Claude after the
audit numbers are computed. Reads JSON audit data from stdin or a file, writes a
two-page PostPilot-branded PDF.

Usage:
  python build_audit_pdf.py --input audit.json --output audit.pdf
  cat audit.json | python build_audit_pdf.py --output audit.pdf

Required JSON keys:
  account_name        str
  run_date            str ("June 9, 2026")
  total_subs          int
  active_subs         int
  total_buyers        int          (or null if no ecommerce)
  active_buyers       int          (or null)
  lapsed_buyers       int          (or null)
  vips                int          (or null)
  aov                 float        (or null)
  response_rate       float        (default 0.076)
  send_cost           float        (default 0.75)
  trend_months        list[str]    (e.g. ["Dec", "Jan", ...])
  trend_vals          list[int]    (monthly unique clickers)
  takeaways           list[{title, body}]  (3 items, page 2 body)
"""
import argparse
import json
import os
import sys

# Resolve plugin paths relative to this script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(SCRIPT_DIR)
FONT_DIR = os.path.join(SCRIPT_DIR, "fonts")
ASSETS_DIR = os.path.join(PLUGIN_DIR, "assets")

# --- Lazy imports with helpful errors ---
def _import_deps():
    missing = []
    try:
        import reportlab  # noqa: F401
    except ImportError:
        missing.append("reportlab")
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        missing.append("matplotlib")
    if missing:
        print(
            "ERROR: This script needs Python packages that aren't installed: "
            + ", ".join(missing) + "\n"
            "Install with: pip install --break-system-packages " + " ".join(missing),
            file=sys.stderr,
        )
        sys.exit(2)

_import_deps()

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

# === Font setup with graceful fallback ===
def register_font(name, bundled_path, system_paths):
    """Register a TTF font, preferring the bundled copy."""
    candidates = [bundled_path] + system_paths
    for path in candidates:
        if path and os.path.exists(path):
            pdfmetrics.registerFont(TTFont(name, path))
            return path
    return None

# Try bundled Inter, fall back to a system sans serif
BODY_PATH = register_font(
    "Body",
    os.path.join(FONT_DIR, "Inter-Regular.ttf"),
    ["/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/Library/Fonts/Arial.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
)
BODY_BOLD_PATH = register_font(
    "Body-Bold",
    os.path.join(FONT_DIR, "Inter-Bold.ttf"),
    ["/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
     "/Library/Fonts/Arial Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
)
# Display: system serif (DejaVu Serif on Linux, Times on Mac)
DISPLAY_PATH = register_font(
    "Display",
    os.path.join(FONT_DIR, "Serif-Regular.ttf"),
    ["/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
     "/Library/Fonts/Times New Roman.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"],
)
DISPLAY_BOLD_PATH = register_font(
    "Display-Bold",
    os.path.join(FONT_DIR, "Serif-Bold.ttf"),
    ["/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
     "/Library/Fonts/Times New Roman Bold.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"],
)

if BODY_PATH:
    fm.fontManager.addfont(BODY_PATH)
    # Tell matplotlib which family name the registered font is filed under
    try:
        mpl_name = fm.FontProperties(fname=BODY_PATH).get_name()
        plt.rcParams["font.family"] = mpl_name
    except Exception:
        pass  # fall back to default

LOGO_PATH = os.path.join(ASSETS_DIR, "postpilot-logo-white.png")

# === PostPilot brand tokens ===
OFF_WHITE = HexColor("#F3F0EC")
WHITE = HexColor("#FFFFFF")
BLUE = HexColor("#6AB1F3")
NAVY = HexColor("#398BC7")
CHARCOAL = HexColor("#2E2F34")
LIME = HexColor("#D0F582")
MID_GRAY = HexColor("#D9D6CF")
SOFT_WHITE = HexColor("#C9C7C2")

PW, PH = LETTER
M = 54

# === Canvas helpers ===
def fill_rect(c, x, y, w, h, color):
    c.setFillColor(color)
    c.rect(x, y, w, h, stroke=0, fill=1)

def draw_card(c, x, y, w, h, fill=WHITE, border=MID_GRAY):
    c.setFillColor(fill)
    c.setStrokeColor(border)
    c.setLineWidth(0.5)
    c.roundRect(x, y, w, h, 8, stroke=1, fill=1)

def text(c, x, y, s, font="Body", size=10, color=CHARCOAL, align="left"):
    c.setFillColor(color)
    c.setFont(font, size)
    if align == "right":
        c.drawRightString(x, y, s)
    elif align == "center":
        c.drawCentredString(x, y, s)
    else:
        c.drawString(x, y, s)

def wrap_text(c, s, font, size, max_w):
    c.setFont(font, size)
    words = s.split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if c.stringWidth(trial, font, size) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines

def draw_logo_topright(c, top_y):
    if not os.path.exists(LOGO_PATH):
        return
    logo = ImageReader(LOGO_PATH)
    iw, ih = logo.getSize()
    target_w = 88
    target_h = target_w * ih / iw
    c.drawImage(logo, PW - M - target_w, top_y - target_h - 4,
                width=target_w, height=target_h, mask="auto")

# === Chart ===
def render_chart(data, path):
    months = data["trend_months"]
    vals = data["trend_vals"]
    fig, ax = plt.subplots(figsize=(7.0, 1.6), dpi=200)
    ax.fill_between(range(len(months)), vals, color="#6AB1F3", alpha=0.10)
    ax.plot(range(len(months)), vals, color="#398BC7", linewidth=2.2,
            marker="o", markersize=6, markerfacecolor="#2E2F34",
            markeredgecolor="#FFFFFF", markeredgewidth=1.5)
    ax.set_xticks(range(len(months)))
    ax.set_xticklabels(months, color="#2E2F34", fontsize=9, fontweight="bold")
    ymax = max(vals) * 1.2 if vals else 1
    step = max(1, int(ymax / 3))
    ticks = list(range(0, int(ymax) + step, step))
    ax.set_yticks(ticks)
    ax.set_yticklabels([f"{t/1000:.0f}K" if t else "" for t in ticks],
                       color="#888", fontsize=8)
    ax.set_ylim(0, ymax * 1.05)
    for spine in ("top", "right", "left", "bottom"):
        ax.spines[spine].set_visible(False)
    ax.tick_params(colors="#888", length=0)
    ax.grid(True, axis="y", color="#E5E2DC", linewidth=0.8, alpha=0.9)
    ax.set_axisbelow(True)
    ax.set_facecolor("#FFFFFF")
    fig.patch.set_facecolor("#FFFFFF")
    plt.tight_layout(pad=0.5)
    plt.savefig(path, dpi=200, bbox_inches="tight", facecolor="#FFFFFF")
    plt.close()

# === Page 1 ===
def draw_header_band(c, y_top, label, title, subtitle, height=130):
    fill_rect(c, 0, y_top - height, PW, height, CHARCOAL)
    draw_logo_topright(c, y_top - 28)
    text(c, M, y_top - 44, label.upper(), font="Body-Bold", size=8, color=BLUE)
    text(c, M, y_top - 78, title, font="Display", size=28, color=WHITE)
    if subtitle:
        text(c, M, y_top - 104, subtitle, font="Body", size=10, color=SOFT_WHITE)

def draw_stat_card(c, x, y_top, w, h, label, number, sub):
    draw_card(c, x, y_top - h, w, h)
    text(c, x + 24, y_top - 28, label.upper(), font="Body-Bold", size=8, color=NAVY)
    text(c, x + 24, y_top - 86, number, font="Display", size=48, color=CHARCOAL)
    text(c, x + 24, y_top - 110, sub, font="Body", size=9, color=CHARCOAL)

def draw_trend_card(c, x, y_top, w, h, chart_path):
    draw_card(c, x, y_top - h, w, h)
    text(c, x + 24, y_top - 26, "MONTHLY UNIQUE CLICKERS  ·  LAST 6 MONTHS",
         font="Body-Bold", size=8, color=NAVY)
    img = ImageReader(chart_path)
    iw, ih = img.getSize()
    chart_w = w - 32
    chart_h = chart_w * ih / iw
    c.drawImage(img, x + 16, y_top - 38 - chart_h,
                width=chart_w, height=chart_h, mask="auto")

def draw_opportunity_band(c, y_top, data, height=190):
    fill_rect(c, 0, y_top - height, PW, height, CHARCOAL)
    text(c, M, y_top - 34, "THE OPPORTUNITY", font="Body-Bold", size=8, color=BLUE)

    col_w = (PW - 2*M - 30) / 2
    left_x = M
    right_x = M + col_w + 30
    inner_top = y_top - 64

    rev_at_risk = data.get("revenue_at_risk") or 0
    dm_annual_net = data.get("dm_annual_net") or 0
    dormant_buyers = data.get("dormant_buyers", 0)
    aov = data.get("aov") or DEFAULT_AOV
    estimated_ltv = data.get("estimated_ltv") or (aov * DEFAULT_LTV_MULTIPLIER)
    eff_mult = data.get("effective_ltv_multiplier", DEFAULT_LTV_MULTIPLIER)
    ltv_is_actual = data.get("ltv_is_actual", False)
    onetime_net = data.get("dm_one_time_net") or 0
    onetime_roas = data.get("dm_one_time_roas") or 0
    aov_estimated = data.get("aov_is_estimate", False)
    auto_cvr_pct = data.get("automated_cvr", DEFAULT_AUTOMATED_CVR) * 100
    onetime_cvr_pct = data.get("onetime_cvr", DEFAULT_ONETIME_CVR) * 100

    # Left column: Revenue at risk (LTV-based)
    text(c, left_x, inner_top, "REVENUE AT RISK",
         font="Body-Bold", size=8, color=BLUE)
    text(c, left_x, inner_top - 36, f"${rev_at_risk/1_000_000:.1f}M",
         font="Display", size=38, color=WHITE)
    ltv_source = data.get("ltv_source", "default")
    if ltv_source == "sampled_clv":
        ltv_phrasing = (f"~${estimated_ltv:.0f} historic LTV per buyer "
                        f"(from your Klaviyo predictive analytics, {eff_mult:.1f}x your "
                        f"${aov:.0f}{'*' if aov_estimated else ''} AOV)")
    elif ltv_source == "ttm":
        ltv_phrasing = (f"~${estimated_ltv:.0f} annual revenue per buyer "
                        f"(your trailing 12-month data, {eff_mult:.1f}x your "
                        f"${aov:.0f}{'*' if aov_estimated else ''} AOV)")
    elif ltv_source == "industry_default":
        ltv_phrasing = (f"~${estimated_ltv:.0f} estimated annual LTV "
                        f"({eff_mult:.1f}x your ${aov:.0f}{'*' if aov_estimated else ''} AOV, "
                        f"calibrated to your industry's typical purchase cycle)")
    else:
        ltv_phrasing = (f"~${estimated_ltv:.0f} estimated annual LTV "
                        f"({eff_mult:.1f}x your ${aov:.0f}{'*' if aov_estimated else ''} AOV)")
    explain = (f"in dormant buyer LTV currently unreachable via email. "
               f"{dormant_buyers:,} buyers at {ltv_phrasing}.")
    ly = inner_top - 64
    for ln in wrap_text(c, explain, "Body", 9, col_w):
        text(c, left_x, ly, ln, font="Body", size=9, color=SOFT_WHITE)
        ly -= 14

    # Right column: Direct mail winback (annualized, using automated CVR)
    text(c, right_x, inner_top, "DIRECT MAIL WINBACK  ·  ANNUALIZED",
         font="Body-Bold", size=8, color=BLUE)
    text(c, right_x, inner_top - 36, f"${dm_annual_net/1000:.0f}K",
         font="Display", size=38, color=WHITE)
    explain2 = (f"net annualized revenue from an evergreen automated flow at "
                f"{auto_cvr_pct:.0f}% incremental CVR. "
                f"${onetime_net/1000:.0f}K from a one-time send at "
                f"{onetime_cvr_pct:.0f}% CVR ({onetime_roas:.1f}x ROAS).")
    ly = inner_top - 64
    for ln in wrap_text(c, explain2, "Body", 9, col_w):
        text(c, right_x, ly, ln, font="Body", size=9, color=SOFT_WHITE)
        ly -= 14

    # Footnote if AOV is estimated
    if aov_estimated:
        text(c, M, y_top - height + 16,
             f"* AOV estimated at $50 (a conservative DTC default). "
             f"Pass actual AOV in the audit data for sharper numbers.",
             font="Body", size=7.5, color=SOFT_WHITE)

def render_page1(c, data, chart_path):
    fill_rect(c, 0, 0, PW, PH, OFF_WHITE)
    draw_header_band(c, y_top=PH,
                     label="Klaviyo email dormancy audit",
                     title=data["account_name"],
                     subtitle=f"Run on  {data['run_date']}",
                     height=130)

    body_top = PH - 130 - 28
    card_h = 140
    gap = 16
    col_w = (PW - 2*M - 18) / 2

    sub_pct = data["active_subs"] / data["total_subs"] * 100
    draw_stat_card(c, M, body_top, col_w, card_h,
                   "Email subscribers", f"{sub_pct:.1f}%",
                   f"clicked in 90 days  ·  {data['active_subs']:,} of {data['total_subs']:,}")
    if data.get("total_buyers"):
        buy_pct = data["active_buyers"] / data["total_buyers"] * 100
        draw_stat_card(c, M + col_w + 18, body_top, col_w, card_h,
                       "All-time buyers", f"{buy_pct:.1f}%",
                       f"clicked in 90 days  ·  {data['active_buyers']:,} of {data['total_buyers']:,}")

    # Trend card only if we have real trend data
    has_trend = (data.get("trend_vals") and any(v for v in data["trend_vals"]))
    if has_trend:
        trend_top = body_top - card_h - gap
        trend_h = 160
        draw_trend_card(c, M, trend_top, PW - 2*M, trend_h, chart_path)
        op_top = trend_top - trend_h - 28
    else:
        # No trend data: skip the card, push the opportunity band up
        op_top = body_top - card_h - 36

    draw_opportunity_band(c, y_top=op_top, data=data, height=200)

# === Page 2 ===
def draw_what_this_means(c, y_top, data):
    """Page 2 middle: interpretation + 4 in-Klaviyo moves. Bridge to page 3."""
    y = y_top - 36

    # Section 1: What this means
    text(c, M, y, "WHAT THIS MEANS",
         font="Body-Bold", size=9, color=NAVY); y -= 24
    text(c, M, y, "The dormant slice is where the money is.",
         font="Display", size=24, color=CHARCOAL); y -= 28

    # Personalized interpretation (3-4 sentences)
    sub_pct = data["active_subs"] / data["total_subs"] * 100
    if data.get("total_buyers"):
        buy_pct = data["active_buyers"] / data["total_buyers"] * 100
        diff = buy_pct - sub_pct
        dormant_buyers = data.get("dormant_buyers", 0)
        if diff > 0.5:
            interp = (f"Your buyers are slightly more engaged than subscribers "
                      f"({buy_pct:.1f}% vs {sub_pct:.1f}%), the healthy direction. "
                      f"But {dormant_buyers:,} of your all-time buyers, people who already paid "
                      f"for your product, have stopped engaging with email entirely. "
                      f"Some of that decay is fixable inside Klaviyo. The biggest piece isn't.")
        elif diff < -0.5:
            interp = (f"Your buyers are less engaged than your subscribers "
                      f"({buy_pct:.1f}% vs {sub_pct:.1f}%). That's backwards. "
                      f"Buyers should typically be your most engaged segment. The data points to "
                      f"a post-purchase email flow that's leaking attention, plus deliverability "
                      f"decay across the broader list. Both are fixable.")
        else:
            interp = (f"Buyer and subscriber engagement run roughly equal "
                      f"({buy_pct:.1f}% vs {sub_pct:.1f}%). Either way, "
                      f"{dormant_buyers:,} of your buyers have stopped engaging with email. "
                      f"Some of that decay is fixable inside Klaviyo. The biggest piece isn't.")
    else:
        interp = (f"Only {sub_pct:.1f}% of your subscribers click in a typical 90-day window. "
                  f"The rest aren't gone, they're just out of reach via email. "
                  f"Some of that decay is fixable inside Klaviyo. The biggest piece isn't.")

    for ln in wrap_text(c, interp, "Body", 10.5, PW - 2*M):
        text(c, M, y, ln, font="Body", size=10.5, color=CHARCOAL); y -= 17
    y -= 22

    # Section 2: The five moves to fix this
    text(c, M, y, "FIVE MOVES TO FIX THIS",
         font="Body-Bold", size=9, color=NAVY); y -= 22

    moves_1_4 = [
        ("01", "Suppress your dead subscribers.",
         "Counterintuitive but every serious operator does it. Subscribers who haven't engaged in 9-12 months hurt deliverability for the rest. Engaged subscribers' open and click rates typically jump 30-50% within weeks of cleanup."),
        ("02", "Switch dormants to plain-text re-engagement.",
         "Image-heavy templates increasingly land in secondary tabs. Send dormants a 3-email plain-text series from a real human's name, one question and one link per email. Click rates jump 2-4x."),
        ("03", "Segment your sending frequency.",
         "Mail engaged subscribers 4-6 times a week, dormants only 1-2 times a month. Pause flows for dormants. Stop training Gmail to filter you."),
        ("04", "Catch micro-intent with behavioral triggers.",
         "Even dormants visit your site occasionally. Browse abandon, post-purchase, and replenishment flows convert dramatically better than broadcast sends because they're tied to actual signals of life."),
    ]
    for num, title, body in moves_1_4:
        text(c, M, y, num, font="Display", size=18, color=BLUE)
        text(c, M + 40, y, title, font="Body-Bold", size=10.5, color=CHARCOAL)
        y -= 15
        for ln in wrap_text(c, body, "Body", 9.5, PW - M - 40 - M):
            text(c, M + 40, y, ln, font="Body", size=9.5, color=CHARCOAL)
            y -= 13
        y -= 10

    # Move 5: Direct mail — brief teaser, full math on page 3
    text(c, M, y, "05", font="Display", size=18, color=BLUE)
    text(c, M + 40, y, "Reach high-LTV dormant buyers via direct mail.",
         font="Body-Bold", size=10.5, color=CHARCOAL)
    y -= 15

    if data.get("dormant_buyers"):
        lapsed = data.get("lapsed_buyers") or data.get("dormant_buyers", 0)
        revenue_at_risk = data.get("revenue_at_risk", 0)
        annual_net = data.get("dm_annual_net") or 0
        move5_body = (
            f"Your {lapsed:,} dormant buyers represent ~${revenue_at_risk/1_000_000:.1f}M "
            f"in estimated customer LTV that email can no longer reach. An automated DM "
            f"winback flow nets ~${annual_net/1000:.0f}K/yr at conservative incremental "
            f"CVR assumptions. Full math on the next page."
        )
    else:
        move5_body = (
            "The only off-channel move that works at scale on a list this size. "
            "Direct mail reaches dormant buyers in a way email no longer can. "
            "Full breakdown on the next page."
        )
    for ln in wrap_text(c, move5_body, "Body", 9.5, PW - M - 40 - M):
        text(c, M + 40, y, ln, font="Body", size=9.5, color=CHARCOAL)
        y -= 13

def draw_install_band(c, y_top, height=130):
    """Tighter capstone — Move 5 in the body already pitched. This is just the URL."""
    fill_rect(c, 0, y_top - height, PW, height, CHARCOAL)

    text(c, M, y_top - 34, "BOOK A 30-MIN WALKTHROUGH WITH POSTPILOT",
         font="Body-Bold", size=8, color=BLUE)
    text(c, M, y_top - 58, "Get the audit interpreted, plus a DM strategy sized to your numbers.",
         font="Body", size=10, color=SOFT_WHITE)

    # Big lime CTA — display clean, hyperlink with UTM tracking
    cta_text = "postpilot.com/meet"
    cta_url = ("https://postpilot.com/meet"
               "?utm_source=linkedin"
               "&utm_medium=social"
               "&utm_campaign=claude-klaviyo-audit"
               "&utm_content=claude-klaviyo-audit-v1")
    cta_x = M
    cta_y = y_top - height + 30
    cta_size = 34
    text(c, cta_x, cta_y, cta_text, font="Display", size=cta_size, color=LIME)
    cta_w = c.stringWidth(cta_text, "Display", cta_size)
    c.linkURL(cta_url,
              (cta_x, cta_y - 4, cta_x + cta_w, cta_y + cta_size - 4),
              relative=0, thickness=0)

def render_page2(c, data):
    fill_rect(c, 0, 0, PW, PH, OFF_WHITE)
    draw_header_band(c, y_top=PH,
                     label="The interpretation",
                     title="The dormant majority.",
                     subtitle="Why most of your buyers can no longer be reached by email.",
                     height=130)
    draw_what_this_means(c, y_top=PH - 130 - 8, data=data)


def draw_dm_audience(c, y_top, data):
    """Page 3 top section: who the audience is and what they represent."""
    y = y_top - 36
    text(c, M, y, "THE AUDIENCE",
         font="Body-Bold", size=9, color=NAVY); y -= 22

    lapsed = data.get("lapsed_buyers") or data.get("dormant_buyers", 0)
    total_buyers = data.get("total_buyers", 1)
    dormant_pct = lapsed / total_buyers * 100 if total_buyers else 0
    revenue_at_risk = data.get("revenue_at_risk", 0)
    aov = data.get("aov", DEFAULT_AOV)
    estimated_ltv = data.get("estimated_ltv") or (aov * DEFAULT_LTV_MULTIPLIER)
    eff_mult = data.get("effective_ltv_multiplier", DEFAULT_LTV_MULTIPLIER)
    ltv_is_actual = data.get("ltv_is_actual", False)

    text(c, M, y, f"{lapsed:,} dormant buyers.",
         font="Display", size=26, color=CHARCOAL); y -= 26

    ltv_source = data.get("ltv_source", "default")
    if ltv_source == "sampled_clv":
        ltv_clause = (f"At ~${estimated_ltv:.0f} historic LTV per buyer "
                      f"(averaged from Klaviyo's predictive analytics across your buyer base, "
                      f"{eff_mult:.1f}x your ${aov:.0f} AOV — captures your full multi-year "
                      f"purchase history, not just last 12 months)")
    elif ltv_source == "ttm":
        ltv_clause = (f"At ~${estimated_ltv:.0f} annual revenue per buyer "
                      f"(computed from your trailing 12-month Klaviyo data, "
                      f"{eff_mult:.1f}x your ${aov:.0f} AOV)")
    elif ltv_source == "industry_default":
        ltv_clause = (f"At an estimated ~${estimated_ltv:.0f} annual LTV per buyer "
                      f"({eff_mult:.1f}x your ${aov:.0f} AOV, calibrated to your industry's "
                      f"typical purchase cycle since Klaviyo's predictive analytics weren't available)")
    else:
        ltv_clause = (f"At an estimated annual LTV of ~${estimated_ltv:.0f} per buyer "
                      f"({eff_mult:.1f}x your ${aov:.0f} AOV, conservative DTC default)")
    summary = (f"That's {dormant_pct:.0f}% of your all-time buyer base. People who already "
               f"paid for your product but have stopped engaging with email. "
               f"{ltv_clause}, this cohort represents ~${revenue_at_risk/1_000_000:.1f}M in "
               f"customer relationship value that email can no longer reach.")
    for ln in wrap_text(c, summary, "Body", 10.5, PW - 2*M):
        text(c, M, y, ln, font="Body", size=10.5, color=CHARCOAL); y -= 17
    return y


def draw_dm_math(c, y_top, data):
    """Page 3 middle: two scenarios as cards. One-time at conservative 5%,
    automated evergreen at 8% (automations beat one-time blasts on conversion)."""
    y = y_top - 24
    text(c, M, y, "THE MATH",
         font="Body-Bold", size=9, color=NAVY); y -= 18

    card_w = (PW - 2*M - 16) / 2
    card_h = 188

    onetime_cvr_pct = data.get("onetime_cvr", DEFAULT_ONETIME_CVR) * 100
    auto_cvr_pct = data.get("automated_cvr", DEFAULT_AUTOMATED_CVR) * 100
    aov = data.get("aov", DEFAULT_AOV)
    sc = data.get("send_cost", DEFAULT_SEND_COST)

    # === Card 1: One-time send ===
    draw_card(c, M, y - card_h, card_w, card_h)
    cx = M + 22
    cy = y - 26

    # Card title (charcoal label, no competing navy)
    text(c, cx, cy, "ONE-TIME SEND",
         font="Body-Bold", size=9, color=CHARCOAL); cy -= 14
    text(c, cx, cy, f"{onetime_cvr_pct:.0f}% incremental CVR",
         font="Body", size=9, color=NAVY); cy -= 28

    # Big number — unambiguous as net because the caption below says so
    text(c, cx, cy, f"${(data.get('dm_one_time_net') or 0)/1000:.0f}K",
         font="Display", size=32, color=CHARCOAL); cy -= 22
    text(c, cx, cy,
         f"Net revenue at {data.get('dm_one_time_roas', 0):.1f}x ROAS",
         font="Body", size=9.5, color=CHARCOAL); cy -= 22

    rows1 = [
        ("Audience", f"{(data.get('lapsed_buyers') or 0):,}"),
        ("Reactivations", f"~{int(data.get('dm_one_time_react') or 0):,}"),
        ("Revenue", f"${(data.get('dm_one_time_rev') or 0)/1000:.0f}K"),
        ("Send cost", f"${(data.get('dm_one_time_cost') or 0)/1000:.0f}K"),
    ]
    for label, val in rows1:
        text(c, cx, cy, label, font="Body", size=9, color=CHARCOAL)
        text(c, cx + card_w - 44, cy, val, font="Body-Bold", size=9, color=CHARCOAL, align="right")
        cy -= 12

    # === Card 2: Automated evergreen flow ===
    rx = M + card_w + 16
    draw_card(c, rx, y - card_h, card_w, card_h)
    cx2 = rx + 22
    cy = y - 26

    text(c, cx2, cy, "AUTOMATED EVERGREEN FLOW",
         font="Body-Bold", size=9, color=CHARCOAL); cy -= 14
    text(c, cx2, cy, f"{auto_cvr_pct:.0f}% incremental CVR  ·  automations beat blasts",
         font="Body", size=9, color=NAVY); cy -= 28

    text(c, cx2, cy, f"${(data.get('dm_annual_net') or 0)/1000:.0f}K",
         font="Display", size=32, color=CHARCOAL); cy -= 22
    text(c, cx2, cy,
         f"Net annualized  ·  ${(data.get('dm_monthly_net') or 0)/1000:.0f}K per month",
         font="Body", size=9.5, color=CHARCOAL); cy -= 22

    rows2 = [
        ("Monthly volume", f"~{int(data.get('dm_monthly_vol') or 0):,}"),
        ("Reactivations", f"~{int(data.get('dm_monthly_react') or 0):,}/mo"),
        ("Revenue", f"${(data.get('dm_monthly_rev') or 0)/1000:.1f}K/mo"),
        ("Send cost", f"${(data.get('dm_monthly_cost') or 0)/1000:.1f}K/mo"),
    ]
    for label, val in rows2:
        text(c, cx2, cy, label, font="Body", size=9, color=CHARCOAL)
        text(c, cx2 + card_w - 44, cy, val, font="Body-Bold", size=9, color=CHARCOAL, align="right")
        cy -= 12

    return y - card_h


def draw_dm_sensitivity(c, y_top, data):
    """Show annualized net at different incremental CVR rates. Compact layout."""
    y = y_top - 22
    text(c, M, y, "SENSITIVITY  ·  ANNUALIZED NET AT DIFFERENT INCREMENTAL CVRS",
         font="Body-Bold", size=9, color=NAVY); y -= 18

    aov = data.get("aov", DEFAULT_AOV)
    sc = data.get("send_cost", DEFAULT_SEND_COST)
    lb = data.get("lapsed_buyers") or 0

    def annual_net_at(rate):
        mo_vol = lb / 12
        mo_net = mo_vol * (rate * aov - sc)
        return mo_net * 12

    intro = ("If the actual incremental CVR lands above or below our defaults, the annualized net moves with it.")
    for ln in wrap_text(c, intro, "Body", 9.5, PW - 2*M):
        text(c, M, y, ln, font="Body", size=9.5, color=CHARCOAL); y -= 13
    y -= 4

    scenarios = [
        ("3% CVR (conservative-of-conservative)", annual_net_at(0.03)),
        ("5% CVR (the one-time send baseline)", annual_net_at(0.05)),
        ("8% CVR (the automated flow baseline)", annual_net_at(0.08)),
        ("12% CVR (top-quartile programs hit this)", annual_net_at(0.12)),
    ]
    def fmt(v):
        return f"~${v/1_000_000:.1f}M/yr" if v >= 1_000_000 else f"~${v/1000:.0f}K/yr"

    for label, val in scenarios:
        text(c, M + 12, y, "·", font="Body-Bold", size=11, color=BLUE)
        text(c, M + 28, y, label, font="Body", size=9.5, color=CHARCOAL)
        text(c, PW - M, y, fmt(val),
             font="Body-Bold", size=9.5, color=CHARCOAL, align="right")
        y -= 13

    return y


def draw_dm_cta_band(c, y_top, height=120):
    """Page 3 bottom CTA — charcoal with the lime URL. Compact."""
    fill_rect(c, 0, y_top - height, PW, height, CHARCOAL)
    text(c, M, y_top - 30, "READY TO SIZE THIS FOR YOUR BRAND?",
         font="Body-Bold", size=8, color=BLUE)
    text(c, M, y_top - 54, "Book a 30-min walkthrough with PostPilot.",
         font="Display", size=17, color=WHITE)
    text(c, M, y_top - 74,
         "We size the program, build the audience, and ship the first send.",
         font="Body", size=9.5, color=SOFT_WHITE)

    cta_text = "postpilot.com/meet"
    cta_url = ("https://postpilot.com/meet"
               "?utm_source=linkedin"
               "&utm_medium=social"
               "&utm_campaign=claude-klaviyo-audit"
               "&utm_content=claude-klaviyo-audit-v1")
    cta_x = M
    cta_y = y_top - height + 22
    cta_size = 28
    text(c, cta_x, cta_y, cta_text, font="Display", size=cta_size, color=LIME)
    cta_w = c.stringWidth(cta_text, "Display", cta_size)
    c.linkURL(cta_url,
              (cta_x, cta_y - 4, cta_x + cta_w, cta_y + cta_size - 4),
              relative=0, thickness=0)


def render_page3(c, data):
    """Dedicated direct mail page."""
    fill_rect(c, 0, 0, PW, PH, OFF_WHITE)
    draw_header_band(c, y_top=PH,
                     label="Move five",
                     title="Reach them off-channel.",
                     subtitle="Direct mail to your dormant buyers, sized to your numbers.",
                     height=130)
    y = draw_dm_audience(c, y_top=PH - 130 - 8, data=data)
    y = draw_dm_math(c, y_top=y - 16, data=data)
    y = draw_dm_sensitivity(c, y_top=y - 8, data=data)

    # Install band fixed at bottom. Page 3 layout is tight enough that with the
    # tightened sensitivity table, sensitivity content always ends above the band.
    draw_dm_cta_band(c, y_top=140, height=120)

# === Compute derived fields ===
# Conservative defaults used when actual values aren't passed in. The script
# always computes the opportunity section so the PDF never has empty bands.
DEFAULT_AOV = 50.0
DEFAULT_LTV_MULTIPLIER = 2.0   # Annual LTV ~= 2x AOV (typical DTC repeat behavior).
                               # Used to size revenue-at-risk as a relationship value,
                               # not a single transaction.
DEFAULT_ONETIME_CVR = 0.05     # Conservative incremental CVR for a one-time DM blast
DEFAULT_AUTOMATED_CVR = 0.08   # Higher incremental CVR for automated triggered flows
                               # (better timing, repeat impressions, behavioral context)
DEFAULT_SEND_COST = 0.55       # PostPilot pricing floor for 4x6 postcards

def derive(data):
    """Fill in any missing derived numbers from raw inputs. Always produces
    a renderable opportunity section, defaulting AOV when not provided."""
    data.setdefault("onetime_cvr", DEFAULT_ONETIME_CVR)
    data.setdefault("automated_cvr", DEFAULT_AUTOMATED_CVR)
    data.setdefault("send_cost", DEFAULT_SEND_COST)
    data.setdefault("ltv_multiplier", DEFAULT_LTV_MULTIPLIER)

    # AOV fallback: if missing, default to $50 and flag it
    if not data.get("aov"):
        data["aov"] = DEFAULT_AOV
        data["aov_is_estimate"] = True
    else:
        data["aov_is_estimate"] = False

    # Annual LTV per buyer. Source determines caption phrasing:
    #   "sampled_clv"     — averaged from Klaviyo predictive_analytics.historic_clv
    #                       across sampled buyer profiles (most accurate, captures
    #                       full purchase history)
    #   "ttm"             — computed from trailing 12-month revenue / annual buyers
    #                       (accurate for fast-cycle brands, understates for long-cycle)
    #   "industry_default"— AOV × multiplier picked by industry
    #   None / "default"  — AOV × 2.0 fallback (when nothing else is available)
    if data.get("annual_ltv"):
        data["estimated_ltv"] = data["annual_ltv"]
        data["effective_ltv_multiplier"] = data["annual_ltv"] / data["aov"]
        data.setdefault("ltv_source", "ttm")  # default to ttm if source not specified
    else:
        data["estimated_ltv"] = data["aov"] * data["ltv_multiplier"]
        data["effective_ltv_multiplier"] = data["ltv_multiplier"]
        data.setdefault("ltv_source", "default")
    data["ltv_is_actual"] = data["ltv_source"] in ("sampled_clv", "ttm")

    # Dormant buyers and revenue at risk (now LTV-based, not single-AOV)
    if data.get("total_buyers") and data.get("active_buyers") is not None:
        data["dormant_buyers"] = data["total_buyers"] - data["active_buyers"]
        data["revenue_at_risk"] = data["dormant_buyers"] * data["estimated_ltv"]

    # If lapsed_buyers wasn't set explicitly, default it to dormant_buyers
    if not data.get("lapsed_buyers") and data.get("dormant_buyers"):
        data["lapsed_buyers"] = data["dormant_buyers"]

    # Direct mail winback math
    if data.get("lapsed_buyers"):
        onetime_cvr = data["onetime_cvr"]
        auto_cvr = data["automated_cvr"]
        cost = data["send_cost"]
        lb = data["lapsed_buyers"]
        aov = data["aov"]

        # One-time send economics (using onetime_cvr)
        data["dm_one_time_react"] = lb * onetime_cvr
        data["dm_one_time_rev"] = lb * onetime_cvr * aov
        data["dm_one_time_cost"] = lb * cost
        data["dm_one_time_net"] = data["dm_one_time_rev"] - data["dm_one_time_cost"]
        data["dm_one_time_roas"] = data["dm_one_time_rev"] / max(data["dm_one_time_cost"], 1)

        # Automated evergreen flow (using automated_cvr — higher because triggered)
        mo_vol = lb / 12
        data["dm_monthly_vol"] = mo_vol
        data["dm_monthly_react"] = mo_vol * auto_cvr
        data["dm_monthly_rev"] = mo_vol * auto_cvr * aov
        data["dm_monthly_cost"] = mo_vol * cost
        data["dm_monthly_net"] = data["dm_monthly_rev"] - data["dm_monthly_cost"]
        data["dm_monthly_roas"] = data["dm_monthly_rev"] / max(data["dm_monthly_cost"], 1)
        data["dm_annual_net"] = data["dm_monthly_net"] * 12
    return data

# === Main ===
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", "-i", help="JSON file. Reads stdin if omitted.")
    ap.add_argument("--output", "-o", required=True, help="Output .pdf path.")
    args = ap.parse_args()

    if args.input:
        with open(args.input) as f:
            data = json.load(f)
    else:
        data = json.load(sys.stdin)

    data = derive(data)

    chart_path = "/tmp/_audit_trend.png"
    if data.get("trend_vals") and any(v for v in data["trend_vals"]):
        render_chart(data, chart_path)

    c = canvas.Canvas(args.output, pagesize=LETTER)
    c.setTitle(f"Klaviyo Email Dormancy Audit | {data['account_name']}")
    c.setAuthor("PostPilot")

    render_page1(c, data, chart_path)
    c.showPage()
    render_page2(c, data)
    c.showPage()
    render_page3(c, data)
    c.save()

    print(f"PDF written to {args.output} ({os.path.getsize(args.output):,} bytes)")

if __name__ == "__main__":
    main()
