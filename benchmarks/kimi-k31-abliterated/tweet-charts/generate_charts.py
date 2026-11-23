#!/usr/bin/env python3
"""Generate 5 square (2000x2000, 1:1) Morph-branded Artificial Analysis-style tweet charts."""
import re
import subprocess
import tempfile
from pathlib import Path

OUT_DIR = Path("/home/skeptrune/git_projects/morph/PenHarness/benchmarks/kimi-k31-abliterated/tweet-charts")
OUT_DIR.mkdir(parents=True, exist_ok=True)

CHROME = "/home/skeptrune/git_projects/morph/.playwright-browsers/chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell"
INST_FONTS = "/home/skeptrune/git_projects/morph/tmp/kimi-abl-graphic/fonts"
GEIST_MONO = "/home/skeptrune/git_projects/morph/landing/node_modules/geist/dist/fonts/geist-mono"
LOGOS_DIR = Path("/home/skeptrune/git_projects/morph/landing/public/logos")
MORPH_LOGO_PATH = Path("/home/skeptrune/git_projects/morph/landing/public/brand/morph-logo-on-light.svg")


def load_logo(name: str, color: str = "#111611", size: int = 20) -> str:
    raw = (LOGOS_DIR / f"{name}.svg").read_text()
    svg = raw
    if name == "kimi":
        svg = svg.replace('fill="white"', f'fill="{color}"').replace('fill="#4A9EFF"', 'fill="#3F6212"')
    else:
        svg = re.sub(r'fill="(?:white|#fff|#ffffff)"', f'fill="{color}"', svg, flags=re.I)
    svg = re.sub(r"<title>.*?</title>", "", svg)
    svg = re.sub(r"<svg\b", f'<svg width="{size}" height="{size}" style="display:block;flex-shrink:0"', svg, count=1)
    return svg


def load_morph_logo(height: int = 30) -> str:
    raw = MORPH_LOGO_PATH.read_text()
    width = round(height * (174 / 48))
    return re.sub(r'width="174"\s+height="48"', f'width="{width}" height="{height}"', raw, count=1)


MORPH_LOGO = load_morph_logo(30)

BASE_CSS = f"""
@font-face {{
  font-family: 'Instrument Serif';
  src: url('file://{INST_FONTS}/InstrumentSerif-Regular.ttf') format('truetype');
  font-weight: 400;
}}
@font-face {{
  font-family: 'Instrument Sans';
  src: url('file://{INST_FONTS}/InstrumentSans.ttf') format('truetype');
  font-weight: 400 700;
}}
@font-face {{
  font-family: 'Geist Mono';
  src: url('file://{GEIST_MONO}/GeistMono-Regular.woff2') format('woff2');
  font-weight: 400;
}}
@font-face {{
  font-family: 'Geist Mono';
  src: url('file://{GEIST_MONO}/GeistMono-Medium.woff2') format('woff2');
  font-weight: 500;
}}
@font-face {{
  font-family: 'Geist Mono';
  src: url('file://{GEIST_MONO}/GeistMono-SemiBold.woff2') format('woff2');
  font-weight: 600;
}}
@font-face {{
  font-family: 'Geist Mono';
  src: url('file://{GEIST_MONO}/GeistMono-Bold.woff2') format('woff2');
  font-weight: 700;
}}

:root {{
  --paper: #f6f7f2;
  --panel: #ffffff;
  --border: #e3e6df;
  --grid: #e8ebe4;
  --ink: #111611;
  --ink-2: #3a4138;
  --muted: #5c6459;
  --faint: #8d938a;
  --lime: #99d52a;
  --lime-end: #82bf18;
  --lime-text: #3f6212;
  --lime-bg: #f3fae6;
  --lime-border: #c5e87a;
  --stone: #c9cdc4;
  --stone-light: #dadcd5;
}}

* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{
  width: 1000px;
  height: 1000px;
  background: var(--paper);
  color: var(--ink);
  font-family: 'Instrument Sans', sans-serif;
  -webkit-font-smoothing: antialiased;
  overflow: hidden;
}}

.canvas {{
  width: 1000px;
  height: 1000px;
  padding: 40px 46px 32px;
  display: flex;
  flex-direction: column;
  background: var(--paper);
}}

.top-bar {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}}

.priv-badge {{
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 7px 13px;
  background: var(--ink);
  color: #f5f5f4;
  font-family: 'Geist Mono', monospace;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}}
.priv-dot {{
  width: 7px;
  height: 7px;
  background: var(--lime);
  display: inline-block;
}}

.headline-block {{
  margin-bottom: 18px;
}}
.title {{
  font-family: 'Instrument Serif', Georgia, serif;
  font-weight: 400;
  font-size: 50px;
  line-height: 1.04;
  letter-spacing: -0.01em;
  color: var(--ink);
}}
.footer-left {{
  display: flex;
  flex-direction: column;
  gap: 5px;
}}
.bottom-note {{
  font-size: 13.5px;
  font-weight: 500;
  color: var(--ink-2);
  line-height: 1.3;
}}

.card {{
  flex: 1;
  min-height: 0;
  background: var(--panel);
  border: 1.5px solid var(--border);
  border-radius: 16px;
  padding: 24px 28px 24px;
  display: flex;
  flex-direction: column;
}}

.card-top {{
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 16px;
}}
.card-top-row {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}}

.legend {{
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 18px;
  font-size: 13.5px;
  font-weight: 500;
  color: var(--ink-2);
}}
.legend-item {{
  display: inline-flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}}
.swatch {{
  width: 13px;
  height: 13px;
  border-radius: 3px;
  display: inline-block;
  flex-shrink: 0;
}}
.swatch-hatched {{
  background: repeating-linear-gradient(
    -45deg,
    #a8aea4 0 2.5px,
    #e8ebe4 2.5px 5px
  );
  border: 1px solid #9fa59b;
}}

.kicker-pill {{
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 13px;
  border-radius: 999px;
  background: var(--lime-bg);
  border: 1px solid var(--lime-border);
  color: var(--lime-text);
  font-family: 'Geist Mono', monospace;
  font-size: 12.5px;
  font-weight: 600;
  white-space: nowrap;
}}

.footer {{
  margin-top: 16px;
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 20px;
}}
.source {{
  font-family: 'Geist Mono', monospace;
  font-size: 11px;
  color: var(--muted);
  line-height: 1.4;
}}
.cta {{
  font-family: 'Geist Mono', monospace;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--lime-text);
  white-space: nowrap;
  flex-shrink: 0;
}}
"""


def shell(title: str, extra_css: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{BASE_CSS}
{extra_css}
</style>
</head>
<body>
<div class="canvas">
{body}
</div>
</body>
</html>"""


def header_block(title: str) -> str:
    return f"""
    <div class="top-bar">
      {MORPH_LOGO}
      <div class="priv-badge"><span class="priv-dot"></span>Privileged Access</div>
    </div>
    <div class="headline-block">
      <div class="title">{title}</div>
    </div>"""


def footer_block(note: str, source: str) -> str:
    return f"""
    <div class="footer">
      <div class="footer-left">
        <div class="bottom-note">{note}</div>
        <div class="source">{source}</div>
      </div>
      <div class="cta">morphllm.com/privileged-access</div>
    </div>"""


def chart_1_xbow() -> str:
    rows = [
        {"name": "K3.1<br>Abliterated", "sub": "OpenCode<br>10.3M tok", "val": 96.9, "frac": "63/65", "logo": "kimi", "cls": "hero", "badge": "0 REFUSALS"},
        {"name": "DeepSeek 1017<br>Flash Abliterated", "sub": "OpenCode<br>92.7M tok", "val": 96.9, "frac": "63/65", "logo": "deepseek", "cls": "ds"},
        {"name": "GPT-5.5", "sub": "Codex", "val": 96.9, "frac": "63/65", "logo": "openai", "cls": "ref"},
        {"name": "GPT-5.2", "sub": "Codex", "val": 90.8, "frac": "59/65", "logo": "openai", "cls": "ref"},
        {"name": "GPT-5", "sub": "Codex", "val": 73.8, "frac": "48/65", "logo": "openai", "cls": "ref-soft"},
        {"name": "GPT-5", "sub": "OpenCode", "val": 66.2, "frac": "43/65", "logo": "openai", "cls": "same", "note": "Same harness"},
    ]

    extra_css = """
    .plot-wrap {
      flex: 1;
      display: flex;
      flex-direction: column;
      min-height: 0;
    }
    .plot {
      position: relative;
      flex: 1;
      margin-left: 48px;
      margin-top: 8px;
      display: flex;
      align-items: flex-end;
      justify-content: space-around;
      gap: 18px;
      padding: 0 12px;
      border-bottom: 1.5px solid #c9cdc4;
    }
    .gline {
      position: absolute;
      left: 0;
      right: 0;
      border-top: 1px dashed var(--grid);
      pointer-events: none;
    }
    .gline.zero { border-top: none; }
    .gtick {
      position: absolute;
      left: -48px;
      width: 40px;
      text-align: right;
      transform: translateY(-50%);
      font-family: 'Geist Mono', monospace;
      font-size: 12px;
      color: var(--faint);
    }
    .bcol {
      flex: 1;
      max-width: 118px;
      height: 100%;
      display: flex;
      flex-direction: column;
      justify-content: flex-end;
      align-items: center;
      position: relative;
      z-index: 2;
    }
    .bval {
      font-family: 'Geist Mono', monospace;
      font-size: 19px;
      font-weight: 700;
      color: var(--ink-2);
      line-height: 1;
      background: rgba(255,255,255,0.94);
      padding: 2px 5px;
      border-radius: 3px;
    }
    .bcol.hero .bval { color: var(--lime-text); font-size: 21px; }
    .bcol.ds .bval { color: var(--ink); font-size: 20px; }
    .bfrac {
      margin-top: 4px;
      margin-bottom: 8px;
      font-family: 'Geist Mono', monospace;
      font-size: 12px;
      color: var(--muted);
      background: rgba(255,255,255,0.94);
      padding: 0 4px;
    }
    .bbadge {
      margin-bottom: 7px;
      padding: 4px 8px;
      border-radius: 4px;
      background: var(--lime-bg);
      border: 1px solid var(--lime-border);
      color: var(--lime-text);
      font-family: 'Geist Mono', monospace;
      font-size: 10.5px;
      font-weight: 700;
      letter-spacing: 0.06em;
      white-space: nowrap;
    }
    .bnote {
      margin-bottom: 7px;
      padding: 3px 7px;
      border-radius: 4px;
      background: var(--paper);
      border: 1px solid #c9cdc4;
      color: var(--ink-2);
      font-family: 'Geist Mono', monospace;
      font-size: 10px;
      font-weight: 600;
      white-space: nowrap;
    }
    .brect {
      width: 100%;
      border-radius: 8px 8px 0 0;
      background: var(--stone);
    }
    .bcol.hero .brect {
      background: linear-gradient(180deg, #99d52a 0%, #83c218 100%);
    }
    .bcol.ds .brect {
      background: #111611;
    }
    .bcol.ref-soft .brect {
      background: var(--stone-light);
    }
    .bcol.same .brect {
      background: var(--stone);
      border: 1.5px solid #5c6459;
      border-bottom: none;
    }
    .xrow {
      margin-left: 48px;
      display: flex;
      justify-content: space-around;
      gap: 18px;
      padding: 14px 12px 0;
    }
    .xitem {
      flex: 1;
      max-width: 118px;
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
      gap: 5px;
    }
    .xname {
      font-size: 13.5px;
      font-weight: 600;
      color: var(--ink);
      line-height: 1.2;
    }
    .xitem.hero .xname, .xitem.ds .xname { font-weight: 700; }
    .xsub {
      font-family: 'Geist Mono', monospace;
      font-size: 10.5px;
      color: var(--muted);
      line-height: 1.3;
    }
    .xitem.hero .xsub { color: var(--lime-text); font-weight: 600; }
    """

    scale = 0.78
    grid = ""
    for tick in [0, 20, 40, 60, 80, 100]:
        b = tick * scale
        z = " zero" if tick == 0 else ""
        grid += f'<div class="gline{z}" style="bottom:{b:.2f}%"><span class="gtick">{tick}%</span></div>\n'

    bars = ""
    xlabels = ""
    for r in rows:
        badge = f'<div class="bbadge">{r["badge"]}</div>' if r.get("badge") else (f'<div class="bnote">{r["note"]}</div>' if r.get("note") else "")
        h = r["val"] * scale
        bars += f"""
        <div class="bcol {r['cls']}">
          {badge}
          <div class="bval">{r['val']:.1f}%</div>
          <div class="bfrac">{r['frac']}</div>
          <div class="brect" style="height:{h:.2f}%"></div>
        </div>"""
        icon_color = "#111611" if r["cls"] in ("hero", "ds") else "#6c7369"
        logo = load_logo(r["logo"], icon_color, 20)
        xlabels += f"""
        <div class="xitem {r['cls']}">
          {logo}
          <div class="xname">{r['name']}</div>
          <div class="xsub">{r['sub']}</div>
        </div>"""

    body = f"""
    {header_block("K3.1 Abliterated solves 96.9%<br>on XBOW with zero refusals")}

    <div class="card">
      <div class="card-top">
        <div class="legend">
          <span class="legend-item"><span class="swatch" style="background:var(--lime)"></span>K3.1 Abliterated</span>
          <span class="legend-item"><span class="swatch" style="background:var(--ink)"></span>DeepSeek 1017 Flash Abliterated</span>
          <span class="legend-item"><span class="swatch" style="background:var(--stone)"></span>Closed frontier (pass 1)</span>
        </div>
        <div class="kicker-pill">+30.7 pts vs GPT-5 on OpenCode · 9x fewer tokens than DeepSeek</div>
      </div>

      <div class="plot-wrap">
        <div class="plot">
          {grid}
          {bars}
        </div>
        <div class="xrow">
          {xlabels}
        </div>
      </div>
    </div>

    {footer_block(
      "Same 65 black-box web challenges, single pass, exact fresh-flag match, no hints",
      "Morph eval via krodalabs harness (arXiv 2607.13085); GPT rows from paper's per-challenge CSV (pass 1). 0 refusals across all 63 saved K3.1 traces."
    )}
    """
    return shell("XBOW Pentesting", extra_css, body)


def chart_2_aa_cyber() -> str:
    rows = [
        {"name": "GPT-6 Sol<br>(Daybreak Blue)", "sub": "Trusted access only", "val": 69, "logo": "openai", "cls": "restricted"},
        {"name": "K3.1<br>Abliterated†", "sub": "#1 public weights", "val": 64, "logo": "kimi", "cls": "hero", "badge": "95% CYBERGYM-E2E (#1)"},
        {"name": "DeepSeek 1017<br>Flash Abliterated†", "sub": "Morph Abliterated", "val": 57, "logo": "deepseek", "cls": "ds"},
        {"name": "Kimi K3", "sub": "Prior generation", "val": 41, "logo": "kimi", "cls": "muted"},
    ]

    extra_css = """
    .plot-wrap {
      flex: 1;
      display: flex;
      flex-direction: column;
      min-height: 0;
    }
    .plot {
      position: relative;
      flex: 1;
      margin-left: 48px;
      margin-top: 10px;
      display: flex;
      align-items: flex-end;
      justify-content: space-around;
      gap: 36px;
      padding: 0 28px;
      border-bottom: 1.5px solid #c9cdc4;
    }
    .gline {
      position: absolute;
      left: 0;
      right: 0;
      border-top: 1px dashed var(--grid);
      pointer-events: none;
    }
    .gline.zero { border-top: none; }
    .gtick {
      position: absolute;
      left: -48px;
      width: 40px;
      text-align: right;
      transform: translateY(-50%);
      font-family: 'Geist Mono', monospace;
      font-size: 12px;
      color: var(--faint);
    }
    .bcol {
      flex: 1;
      max-width: 152px;
      height: 100%;
      display: flex;
      flex-direction: column;
      justify-content: flex-end;
      align-items: center;
      position: relative;
      z-index: 2;
    }
    .bval {
      font-family: 'Geist Mono', monospace;
      font-size: 24px;
      font-weight: 700;
      color: var(--ink-2);
      margin-bottom: 8px;
      background: rgba(255,255,255,0.94);
      padding: 2px 6px;
    }
    .bcol.hero .bval { color: var(--lime-text); font-size: 26px; }
    .bcol.ds .bval { color: var(--ink); }
    .bbadge {
      margin-bottom: 7px;
      padding: 4px 10px;
      border-radius: 4px;
      background: var(--lime-bg);
      border: 1px solid var(--lime-border);
      color: var(--lime-text);
      font-family: 'Geist Mono', monospace;
      font-size: 10.5px;
      font-weight: 700;
      letter-spacing: 0.04em;
      white-space: nowrap;
    }
    .brect {
      width: 100%;
      border-radius: 9px 9px 0 0;
    }
    .bcol.hero .brect {
      background: linear-gradient(180deg, #99d52a 0%, #83c218 100%);
    }
    .bcol.ds .brect {
      background: #111611;
    }
    .bcol.restricted .brect {
      background: repeating-linear-gradient(-45deg, #b0b6ac 0 5px, #e3e6df 5px 10px);
      border: 1.5px solid #8d938a;
      border-bottom: none;
    }
    .bcol.muted .brect {
      background: var(--stone-light);
    }
    .xrow {
      margin-left: 48px;
      display: flex;
      justify-content: space-around;
      gap: 36px;
      padding: 14px 28px 0;
    }
    .xitem {
      flex: 1;
      max-width: 152px;
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
      gap: 5px;
    }
    .xname {
      font-size: 14.5px;
      font-weight: 600;
      color: var(--ink);
      line-height: 1.2;
    }
    .xitem.hero .xname, .xitem.ds .xname { font-weight: 700; }
    .xsub {
      font-family: 'Geist Mono', monospace;
      font-size: 11px;
      color: var(--muted);
    }
    .xitem.hero .xsub { color: var(--lime-text); font-weight: 600; }
    .callout {
      margin-top: 18px;
      padding: 12px 18px;
      border-radius: 10px;
      background: var(--lime-bg);
      border: 1px solid var(--lime-border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 13.5px;
      font-weight: 600;
      color: var(--lime-text);
    }
    .callout strong {
      font-family: 'Geist Mono', monospace;
      font-size: 14px;
    }
    """

    grid = ""
    for tick in [0, 20, 40, 60, 80]:
        b = (tick / 80) * 82
        z = " zero" if tick == 0 else ""
        grid += f'<div class="gline{z}" style="bottom:{b:.2f}%"><span class="gtick">{tick}%</span></div>\n'

    bars = ""
    xlabels = ""
    for r in rows:
        badge = f'<div class="bbadge">{r["badge"]}</div>' if r.get("badge") else ""
        h = (r["val"] / 80) * 82
        bars += f"""
        <div class="bcol {r['cls']}">
          {badge}
          <div class="bval">{r['val']}%</div>
          <div class="brect" style="height:{h:.2f}%"></div>
        </div>"""
        icon_color = "#111611" if r["cls"] in ("hero", "ds") else "#6c7369"
        logo = load_logo(r["logo"], icon_color, 20)
        xlabels += f"""
        <div class="xitem {r['cls']}">
          {logo}
          <div class="xname">{r['name']}</div>
          <div class="xsub">{r['sub']}</div>
        </div>"""

    body = f"""
    {header_block("K3.1 Abliterated sits within<br>5 points of Daybreak Blue")}

    <div class="card">
      <div class="card-top">
        <div class="legend">
          <span class="legend-item"><span class="swatch" style="background:var(--lime)"></span>K3.1 Abliterated</span>
          <span class="legend-item"><span class="swatch" style="background:var(--ink)"></span>DeepSeek 1017 Flash Abliterated</span>
          <span class="legend-item"><span class="swatch swatch-hatched"></span>Trusted-access only</span>
        </div>
      </div>

      <div class="plot-wrap">
        <div class="plot">
          {grid}
          {bars}
        </div>
        <div class="xrow">
          {xlabels}
        </div>
      </div>

      <div class="callout">
        <span>CyberGym-E2E-AA sub-benchmark (#1 model tested)</span>
        <strong>K3.1 95% vs Daybreak Blue 93%</strong>
      </div>
    </div>

    {footer_block(
      "Artificial Analysis Cyber Index: CWE-Bench-AA, DeepsecBench-AA, and CyberGym-E2E-AA",
      "Source: Artificial Analysis Cyber Index (Oct 2026). †Measured on stock base weights; Morph paired evals confirm abliteration parity (63/65 XBOW)."
    )}
    """
    return shell("Artificial Analysis Cyber Index", extra_css, body)


def chart_3_exploitgym() -> str:
    rows = [
        {"name": "Gemini 4 Carbon", "val": 53.5, "logo": "gemini", "cls": "closed"},
        {"name": "GPT-6 Cyber", "val": 48.5, "logo": "openai", "cls": "closed"},
        {"name": "GPT-6 Astra", "val": 42.4, "logo": "openai", "cls": "closed"},
        {"name": "Claude Opus 5.5", "val": 33.3, "logo": "anthropic", "cls": "closed"},
        {"name": "GPT-6 Sol", "val": 22.1, "logo": "openai", "cls": "closed"},
        {"name": "K3.1 Abliterated†", "val": 20.7, "logo": "kimi", "cls": "hero", "tag": "#1 OPEN"},
        {"name": "DeepSeek 1017 Flash Abliterated†", "val": 15.3, "logo": "deepseek", "cls": "ds"},
        {"name": "GLM-5.3", "val": 15.0, "logo": "zai", "cls": "open"},
    ]

    extra_css = """
    .hwrap {
      flex: 1;
      display: flex;
      flex-direction: column;
      min-height: 0;
      margin-top: 6px;
    }
    .hrows {
      flex: 1;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }
    .hrow {
      display: grid;
      grid-template-columns: 310px 1fr 78px;
      align-items: center;
      gap: 16px;
      padding: 10px 14px;
      border-radius: 10px;
    }
    .hrow.hero {
      background: var(--lime-bg);
      border: 1.5px solid var(--lime-border);
      padding: 14px 14px;
    }
    .hlabel {
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 15px;
      font-weight: 500;
      color: var(--ink-2);
      white-space: nowrap;
    }
    .hrow.hero .hlabel { font-weight: 700; color: var(--ink); font-size: 16px; }
    .hrow.ds .hlabel { font-weight: 700; color: var(--ink); }
    .htag {
      font-family: 'Geist Mono', monospace;
      font-size: 10px;
      font-weight: 700;
      padding: 2.5px 7px;
      border-radius: 4px;
      background: #d9f99d;
      color: var(--lime-text);
      letter-spacing: 0.04em;
    }
    .htrack {
      height: 26px;
      background: var(--paper);
      border-radius: 5px;
      overflow: hidden;
    }
    .hrow.hero .htrack { height: 30px; background: #e8f5cc; }
    .hfill {
      height: 100%;
      border-radius: 5px;
    }
    .hrow.closed .hfill {
      background: repeating-linear-gradient(-45deg, #c2c7be 0 4.5px, #e3e6df 4.5px 9px);
      border-right: 1.5px solid #9ba197;
    }
    .hrow.hero .hfill {
      background: linear-gradient(90deg, #83c218 0%, #99d52a 100%);
    }
    .hrow.ds .hfill { background: #111611; }
    .hrow.open .hfill { background: var(--stone); }
    .hval {
      font-family: 'Geist Mono', monospace;
      font-size: 16.5px;
      font-weight: 600;
      text-align: right;
      color: var(--ink-2);
    }
    .hrow.hero .hval { color: var(--lime-text); font-weight: 700; font-size: 18px; }
    .hrow.ds .hval { color: var(--ink); font-weight: 700; }
    .haxis {
      display: grid;
      grid-template-columns: 310px 1fr 78px;
      gap: 16px;
      padding: 10px 14px 0;
      margin-top: 8px;
      border-top: 1px solid var(--grid);
    }
    .hticks {
      display: flex;
      justify-content: space-between;
      font-family: 'Geist Mono', monospace;
      font-size: 11.5px;
      color: var(--faint);
    }
    """

    hrows = ""
    for r in rows:
        icon_color = "#111611" if r["cls"] in ("hero", "ds") else "#6c7369"
        logo = load_logo(r["logo"], icon_color, 19)
        tag = f'<span class="htag">{r["tag"]}</span>' if r.get("tag") else ""
        w = (r["val"] / 60) * 100
        hrows += f"""
        <div class="hrow {r['cls']}">
          <div class="hlabel">{logo}<span>{r['name']}</span>{tag}</div>
          <div class="htrack"><div class="hfill" style="width:{w:.2f}%"></div></div>
          <div class="hval">{r['val']:.1f}%</div>
        </div>"""

    body = f"""
    {header_block("K3.1 Abliterated leads all<br>open-weight models on ExploitGym")}

    <div class="card">
      <div class="card-top">
        <div class="legend">
          <span class="legend-item"><span class="swatch" style="background:var(--lime)"></span>K3.1 Abliterated</span>
          <span class="legend-item"><span class="swatch" style="background:var(--ink)"></span>DeepSeek 1017 Flash Abliterated</span>
          <span class="legend-item"><span class="swatch swatch-hatched"></span>Closed frontier</span>
        </div>
        <div class="kicker-pill">+35% relative ahead of DeepSeek 1017 Flash Abliterated</div>
      </div>

      <div class="hwrap">
        <div class="hrows">
          {hrows}
        </div>
        <div class="haxis">
          <div></div>
          <div class="hticks"><span>0%</span><span>15%</span><span>30%</span><span>45%</span><span>60%</span></div>
          <div></div>
        </div>
      </div>
    </div>

    {footer_block(
      "End-to-end exploit synthesis across 898 userspace, V8, and Linux kernel tasks",
      "Sources: benchlm.ai / LLMBoard ExploitGym (Nov 19, 2026) &amp; Moonshot K3.1 Pro card (6 h budget). †Measured on stock base weights."
    )}
    """
    return shell("ExploitGym End-to-End Exploits", extra_css, body)


def chart_4_vs_deepseek() -> str:
    rows = [
        {"label": "BountyBench<br>Exploit*", "k": 70.0, "d": 40.0, "delta": "+30.0"},
        {"label": "Terminal-<br>Bench 4.0†", "k": 65.7, "d": 42.3, "delta": "+23.4"},
        {"label": "AA Cyber<br>Index†", "k": 64.0, "d": 57.0, "delta": "+7.0"},
        {"label": "SWE<br>Marathon†", "k": 51.7, "d": 40.0, "delta": "+11.7"},
        {"label": "AA Intel.<br>Index v5†", "k": 46.0, "d": 32.0, "delta": "+14"},
    ]

    extra_css = """
    .plot-wrap {
      flex: 1;
      display: flex;
      flex-direction: column;
      min-height: 0;
    }
    .plot {
      position: relative;
      flex: 1;
      margin-left: 44px;
      margin-top: 8px;
      display: flex;
      align-items: flex-end;
      justify-content: space-around;
      gap: 20px;
      padding: 0 14px;
      border-bottom: 1.5px solid #c9cdc4;
    }
    .gline {
      position: absolute;
      left: 0;
      right: 0;
      border-top: 1px dashed var(--grid);
      pointer-events: none;
    }
    .gline.zero { border-top: none; }
    .gtick {
      position: absolute;
      left: -44px;
      width: 36px;
      text-align: right;
      transform: translateY(-50%);
      font-family: 'Geist Mono', monospace;
      font-size: 12px;
      color: var(--faint);
    }
    .grp {
      flex: 1;
      height: 100%;
      position: relative;
      display: flex;
      align-items: flex-end;
      justify-content: center;
      gap: 8px;
      z-index: 2;
    }
    .dpill {
      position: absolute;
      top: 0;
      left: 50%;
      transform: translateX(-50%);
      font-family: 'Geist Mono', monospace;
      font-size: 11.5px;
      font-weight: 700;
      padding: 3px 9px;
      border-radius: 4px;
      background: var(--lime-bg);
      border: 1px solid var(--lime-border);
      color: var(--lime-text);
      white-space: nowrap;
    }
    .bwrap {
      width: 50px;
      height: 100%;
      display: flex;
      flex-direction: column;
      justify-content: flex-end;
      align-items: center;
    }
    .bnum {
      font-family: 'Geist Mono', monospace;
      font-size: 15px;
      font-weight: 700;
      color: var(--ink);
      margin-bottom: 6px;
      background: rgba(255,255,255,0.94);
      padding: 0 4px;
    }
    .bnum.green { color: var(--lime-text); }
    .bbar {
      width: 100%;
      border-radius: 7px 7px 0 0;
    }
    .bbar.lime { background: linear-gradient(180deg, #99d52a 0%, #83c218 100%); }
    .bbar.ds { background: #111611; }
    .xrow {
      margin-left: 44px;
      display: flex;
      justify-content: space-around;
      gap: 20px;
      padding: 14px 14px 0;
    }
    .xitem {
      flex: 1;
      text-align: center;
      font-size: 14px;
      font-weight: 600;
      color: var(--ink);
      line-height: 1.25;
    }
    .retention-bar {
      margin-top: 20px;
      padding: 13px 18px;
      border-radius: 10px;
      background: var(--paper);
      border: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      gap: 6px;
      font-size: 13px;
      color: var(--ink-2);
    }
    .ret-head {
      font-family: 'Geist Mono', monospace;
      font-size: 10.5px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
    }
    .ret-vals {
      display: flex;
      justify-content: space-between;
      font-family: 'Geist Mono', monospace;
      font-size: 12.5px;
    }
    .ret-vals strong { color: var(--lime-text); }
    """

    # Pixel-exact 0..80 scale mapped to 0..80% height
    grid = ""
    for tick in [0, 20, 40, 60, 80]:
        b = (tick / 80) * 80
        z = " zero" if tick == 0 else ""
        grid += f'<div class="gline{z}" style="bottom:{b:.2f}%"><span class="gtick">{tick}</span></div>\n'

    groups = ""
    xlabels = ""
    for r in rows:
        hk = (r["k"] / 80) * 80
        hd = (r["d"] / 80) * 80
        kstr = f"{r['k']:.0f}" if r["k"] in (70, 64, 46, 35) else f"{r['k']:.1f}"
        dstr = f"{r['d']:.0f}" if r["d"] in (40, 57, 32, 19) else f"{r['d']:.1f}"
        groups += f"""
        <div class="grp">
          <div class="dpill">{r['delta']}</div>
          <div class="bwrap">
            <div class="bnum green">{kstr}</div>
            <div class="bbar lime" style="height:{hk:.2f}%"></div>
          </div>
          <div class="bwrap">
            <div class="bnum">{dstr}</div>
            <div class="bbar ds" style="height:{hd:.2f}%"></div>
          </div>
        </div>"""
        xlabels += f'<div class="xitem">{r["label"]}</div>'

    body = f"""
    {header_block("K3.1 Abliterated vs DeepSeek<br>1017 Flash Abliterated")}

    <div class="card">
      <div class="card-top">
        <div class="legend">
          <span class="legend-item"><span class="swatch" style="background:var(--lime)"></span>K3.1 Abliterated (2.8T / 104B)</span>
          <span class="legend-item"><span class="swatch" style="background:var(--ink)"></span>DeepSeek 1017 Flash Abliterated (552B / 16B)</span>
        </div>
      </div>

      <div class="plot-wrap">
        <div class="plot">
          {grid}
          {groups}
        </div>
        <div class="xrow">
          {xlabels}
        </div>
      </div>

      <div class="retention-bar">
        <div class="ret-head">Abliteration Intelligence Check on K3.1 Abliterated (Morph NeMo-Skills, temp 1.0)</div>
        <div class="ret-vals">
          <span>AIME 2025: <strong>99.3%</strong> (stock 99.8%)</span>
          <span>GPQA Diamond: <strong>91.9%</strong> (stock 94.2%)</span>
          <span>IFBench: <strong>68.0%</strong> (stock 69.3%)</span>
        </div>
      </div>
    </div>

    {footer_block(
      "2.8T / 104B active vs 552B / 16B active decode (6.5x active parameters, higher is better)",
      "*BountyBench Exploit: Morph 10-bounty pilot (C-Agent, abliterated arms; 7/10 incl. django:0 rerun, 6/10 measured; 5/8 vs 4/8 clean). †Stock baselines."
    )}
    """
    return shell("K3.1 Abliterated vs DeepSeek 1017 Flash Abliterated", extra_css, body)


def chart_5_lora_economics() -> str:
    extra_css = """
    .stack {
      flex: 1;
      display: flex;
      flex-direction: column;
      gap: 20px;
      margin-top: 4px;
      min-height: 0;
    }
    .box {
      border: 1.5px solid var(--border);
      background: var(--paper);
      border-radius: 14px;
      padding: 24px 28px;
      display: flex;
      flex-direction: column;
    }
    .box.hero {
      flex: 1;
      background: #ffffff;
      border: 2.5px solid var(--lime);
      box-shadow: 0 10px 30px rgba(153, 213, 42, 0.12);
    }
    .box-head {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 16px;
    }
    .bkicker {
      font-family: 'Geist Mono', monospace;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .box.hero .bkicker { color: var(--lime-text); }
    .btitle {
      margin-top: 4px;
      font-family: 'Instrument Serif', Georgia, serif;
      font-size: 32px;
      font-weight: 400;
      line-height: 1.08;
      color: var(--ink);
    }
    .stat-right {
      text-align: right;
      flex-shrink: 0;
    }
    .big-stat {
      font-family: 'Geist Mono', monospace;
      font-size: 36px;
      font-weight: 700;
      letter-spacing: -0.03em;
      color: var(--ink-2);
      line-height: 1;
    }
    .box.hero .big-stat { color: var(--lime-text); font-size: 40px; }
    .big-sub {
      margin-top: 5px;
      font-family: 'Geist Mono', monospace;
      font-size: 11.5px;
      color: var(--muted);
    }
    .points {
      margin-top: 18px;
      display: flex;
      flex-direction: column;
      gap: 10px;
      font-size: 15px;
      color: var(--ink-2);
      line-height: 1.35;
    }
    .pt {
      display: flex;
      align-items: center;
      gap: 11px;
    }
    .dot {
      width: 8px;
      height: 8px;
      border-radius: 2px;
      background: var(--stone);
      flex-shrink: 0;
    }
    .box.hero .dot { background: var(--lime); }
    .box-foot {
      margin-top: auto;
      padding-top: 14px;
      border-top: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      font-family: 'Geist Mono', monospace;
      font-size: 13px;
    }
    .box:not(.hero) .box-foot { margin-top: 16px; }
    .box-foot span { color: var(--muted); }
    .box-foot strong { color: #b91c1c; }
    .box.hero .box-foot strong { color: var(--lime-text); font-size: 15px; }
    """

    body = f"""
    {header_block("16 MB rank-1 LoRA instead of<br>a second 1.56 TB cluster")}

    <div class="card">
      <div class="card-top">
        <div class="legend">
          <span class="legend-item"><span class="swatch" style="background:var(--lime)"></span>K3.1 Abliterated ($3.25 / $0.38 cached / $18.20)</span>
          <span class="legend-item"><span class="swatch" style="background:var(--ink)"></span>DeepSeek 1017 Flash Abliterated ($0.30 / $0.03 / $1.20)</span>
        </div>
      </div>

      <div class="stack">
        <div class="box">
          <div class="box-head">
            <div>
              <div class="bkicker">Naive deployment</div>
              <div class="btitle">Separate full-weight cluster</div>
            </div>
            <div class="stat-right">
              <div class="big-stat">3.12 TB</div>
              <div class="big-sub">1.56 TB base + 1.56 TB duplicate</div>
            </div>
          </div>
          <div class="points">
            <div class="pt"><span class="dot"></span><span>Bursty pentest swarms leave dedicated GPUs idle between runs</span></div>
            <div class="pt"><span class="dot"></span><span>Duplicate KV cache pools and 2x fixed hardware footprint</span></div>
          </div>
          <div class="box-foot">
            <span>Unit economics</span>
            <strong>Stranded capacity or steep markup</strong>
          </div>
        </div>

        <div class="box hero">
          <div class="box-head">
            <div>
              <div class="bkicker">Morph shared fleet + rank-1 LoRA</div>
              <div class="btitle">One base fleet serving mixed batches</div>
            </div>
            <div class="stat-right">
              <div class="big-stat">1.56 TB + 16 MB</div>
              <div class="big-sub">100,000x smaller delta · 276 matrices · 96.5% BF16 exact</div>
            </div>
          </div>
          <div class="points">
            <div class="pt"><span class="dot"></span><span>Base K3.1 steps stay bit-identical at full speed (zero throughput tax)</span></div>
            <div class="pt"><span class="dot"></span><span>Adapter-keyed KV isolation across GPU, RAM &amp; NVMe (0 cross-hits)</span></div>
            <div class="pt"><span class="dot"></span><span>Isolated privileged authority with Zero Data Retention</span></div>
          </div>
          <div class="box-foot">
            <span>Unit economics</span>
            <strong>+30% over stock K3.1 (no second fleet)</strong>
          </div>
        </div>
      </div>
    </div>

    {footer_block(
      "How Morph serves K3.1 Abliterated on our shared K3.1 fleet at +30% over stock",
      "Morph post-training &amp; serving stack: SVD rank-1 LoRA extraction (99.5% within 1 BF16 ULP), specialized rank-1 LoRA decode, adapter-keyed KV store."
    )}
    """
    return shell("16 MB Rank-1 LoRA Economics", extra_css, body)


CHARTS = [
    ("01-xbow-pentest", chart_1_xbow),
    ("02-aa-cyber-index", chart_2_aa_cyber),
    ("03-exploitgym", chart_3_exploitgym),
    ("04-k31-vs-deepseek", chart_4_vs_deepseek),
    ("05-16mb-lora-economics", chart_5_lora_economics),
]


def main():
    for old in OUT_DIR.glob("*.png"):
        old.unlink()
    for old in OUT_DIR.glob("*.html"):
        old.unlink()

    with tempfile.TemporaryDirectory(prefix="aa-morph-sq-", dir="/tmp/opencode") as tmpdir:
        for slug, fn in CHARTS:
            html_path = OUT_DIR / f"{slug}.html"
            png_path = OUT_DIR / f"{slug}.png"
            html_path.write_text(fn())
            cmd = [
                CHROME,
                "--headless",
                f"--user-data-dir={tmpdir}/{slug}",
                "--no-sandbox",
                "--disable-gpu",
                "--hide-scrollbars",
                "--allow-file-access-from-files",
                "--force-device-scale-factor=2",
                "--window-size=1000,1000",
                "--timeout=10000",
                f"--screenshot={png_path}",
                f"file://{html_path}",
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"Rendered {png_path} ({png_path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
