"""Offline presentation helpers shared by the Streamlit control plane.

Colours and typography follow report_assets.CSS. These helpers only present
recorded state; they do not inspect biological evidence or run a workflow.
"""
from html import escape
from pathlib import Path

NAVY = "#101f33"
TEAL = "#087c78"
GOLD = "#d89925"

CSS = r"""
<style>
:root {--basis-navy:#101f33;--basis-ink:#1b2d40;--basis-muted:#5e6c79;--basis-teal:#087c78;--basis-line:#dce3e8;--basis-gold:#d89925;}
.stApp {background:#f5f6f4;color:var(--basis-ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}
[data-testid="stHeader"] {background:rgba(245,246,244,.92);}
.block-container {max-width:1480px;padding:2.1rem 3rem 4rem;}
h1,h2,h3,h4 {color:var(--basis-navy);letter-spacing:-.025em;font-weight:730;}
h2 {font-size:1.45rem;} h3 {font-size:1.1rem;}
[data-testid="stCaptionContainer"] {color:var(--basis-muted);}
section[data-testid="stSidebar"] {background:var(--basis-navy);border-right:1px solid #254057;}
section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {padding-top:1.5rem;}
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"],section[data-testid="stSidebar"] [data-testid="stCaptionContainer"],section[data-testid="stSidebar"] summary {color:#b7c9d7;}
section[data-testid="stSidebar"] a {color:#b1ded8;}
section[data-testid="stSidebar"] button[kind="secondary"] {background:transparent;border-color:transparent;color:#bfd0dc;text-align:left;justify-content:flex-start;box-shadow:none;padding:.6rem .8rem;}
section[data-testid="stSidebar"] button[kind="secondary"]:hover {background:#223b53;border-color:#34516a;color:#fff;}
section[data-testid="stSidebar"] button[kind="primary"] {background:#223b53;border:1px solid #34516a;border-left:3px solid #69cdc1;color:white;text-align:left;justify-content:flex-start;padding:.6rem .8rem;box-shadow:none;}
section[data-testid="stSidebar"] [data-testid="stExpander"] {background:transparent;border-color:#34495d;}
section[data-testid="stSidebar"] label {color:#bfd0dc;}
.basis-brand {display:flex;align-items:center;gap:12px;padding:0 8px 27px;}
.basis-brand-mark {display:grid;place-items:center;width:43px;height:43px;border-radius:12px;border:1px solid #608b99;color:#abe1d9;background:linear-gradient(140deg,#31596b,#173143);font-size:25px;font-weight:800;}
.basis-brand strong {display:block;color:#fff;font-size:28px;letter-spacing:-1px;line-height:1;}
.basis-brand small {display:block;color:#a9bfcd;font-size:9px;letter-spacing:1.8px;margin-top:8px;}
.basis-nav-caption {padding:14px 10px 6px;color:#94aebf;font-size:10px;letter-spacing:1.8px;text-transform:uppercase;font-weight:750;}
.basis-sidebar-study {border-top:1px solid #34495d;margin:22px 8px 0;padding:20px 0 0;color:#a8bbc9;font-size:12px;overflow-wrap:anywhere;}
.basis-sidebar-study strong {display:block;color:#dce7ed;font-size:13px;margin:5px 0;}
.basis-breadcrumb {display:flex;justify-content:space-between;align-items:center;color:#6b7b88;font-size:12px;margin:0 0 21px;gap:15px;}
.basis-breadcrumb strong {color:#294a5e;font-weight:650;}
.basis-local {background:#e8efed;border:1px solid #d5e3df;border-radius:20px;color:#416b65;padding:4px 10px;font-size:10px;white-space:nowrap;}
.basis-hero {display:flex;align-items:center;justify-content:space-between;gap:26px;isolation:isolate;position:relative;overflow:hidden;padding:34px 38px;border-radius:18px;background:radial-gradient(ellipse at 100% 0,#246276,transparent 50%),linear-gradient(115deg,#142d43,#173e52);color:white;margin:0 0 28px;box-shadow:0 10px 28px #16384b10;}
.basis-hero h1 {color:white;margin:9px 0 13px;font-size:clamp(28px,2.8vw,42px);letter-spacing:-1.1px;line-height:1.15;}
.basis-hero p {margin:0;max-width:760px;color:#dbe8ef;font-size:14px;line-height:1.8;}
.basis-eyebrow {font-size:10px;font-weight:750;letter-spacing:2px;text-transform:uppercase;color:#a9ddd6;}
.basis-hero-meta {display:flex;gap:24px;flex-wrap:wrap;margin-top:24px;padding-top:19px;border-top:1px solid #729cab55;color:#bdd2de;font-size:11px;}
.basis-hero-meta strong {color:#fff;font-weight:650;}
.basis-hero-art {width:210px;flex:0 0 210px;opacity:.95;}
.basis-step {font-size:10px;font-weight:750;letter-spacing:1.5px;text-transform:uppercase;color:var(--basis-teal);margin-bottom:10px;}
.basis-section {display:flex;gap:12px;align-items:center;margin:8px 0 17px;}
.basis-section span {display:grid;place-items:center;width:32px;height:32px;border:1px solid #bdccda;border-radius:8px;color:#486982;font-size:11px;font-family:ui-monospace,monospace;}
.basis-section h2 {margin:0;font-size:23px;}
.basis-note {border-left:3px solid #cda75e;background:#fffbf2;padding:14px 17px;color:#745b31;font-size:12px;line-height:1.65;border-radius:0 8px 8px 0;margin:14px 0;}
.basis-phase-plan {display:grid;grid-template-columns:repeat(3,1fr);gap:0;background:#fff;border:1px solid var(--basis-line);border-radius:13px;overflow:hidden;margin:16px 0 25px;}
.basis-phase {padding:18px 23px;border-right:1px solid #e3e9ed;}
.basis-phase:last-child {border-right:0;}
.basis-phase small {font-size:10px;letter-spacing:1.1px;text-transform:uppercase;color:#7c8790;}
.basis-phase strong {display:block;font-size:14px;color:#294f61;margin:5px 0;}
.basis-phase p {margin:0;color:#6a7d89;font-size:12px;line-height:1.5;}
.basis-state {display:inline-block;padding:5px 10px;border:1px solid #cbdaf3;border-radius:7px;background:#eaf1fc;color:#275393;font-size:11px;font-weight:700;}
.basis-state.complete {background:#e8f5ef;color:#086854;border-color:#c0e4d4;}.basis-state.attention {background:#fff3d8;color:#89500b;border-color:#ecd79f;}.basis-state.failed {background:#fcecf0;color:#9a273b;border-color:#edc3ca;}
.basis-empty {padding:30px;background:#fff;border:1px dashed #b6cbd3;border-radius:14px;color:#5c7481;line-height:1.8;margin:12px 0;}
.basis-empty strong {display:block;color:#204757;font-size:18px;margin-bottom:5px;}
[data-testid="stMetric"] {background:#fff;border:1px solid var(--basis-line);border-radius:13px;padding:19px 22px;position:relative;box-shadow:0 3px 18px #192f4108;}
[data-testid="stMetric"]:before {content:"";position:absolute;top:0;left:22px;width:29px;height:3px;background:var(--basis-teal);border-radius:0 0 4px 4px;}
[data-testid="stMetricLabel"] {font-size:11px;color:#617585;}
[data-testid="stMetricValue"] {color:var(--basis-navy);font-size:30px;font-weight:750;}
[data-testid="stVerticalBlockBorderWrapper"]>div {background:#fff;border-color:var(--basis-line);border-radius:14px;}
[data-testid="stExpander"] {background:#fff;border:1px solid var(--basis-line);border-radius:12px;}
button[kind="primary"],a[kind="primary"] {background:#087c78;border-color:#087c78;border-radius:8px;}
button[kind="primary"]:hover,a[kind="primary"]:hover {background:#066662;border-color:#066662;}
button[kind="secondary"],a[kind="secondary"] {border-color:#c8d6dc;border-radius:8px;background:#fff;color:#28495b;}
button:focus-visible,a:focus-visible {outline:3px solid #d89925!important;outline-offset:3px!important;}
[data-testid="stTextInput"] input,[data-testid="stNumberInput"] input {background:#fff;}
[data-testid="stCode"] {border:1px solid #d5e1e6;border-radius:11px;}
[data-testid="stDataFrame"] {border-radius:10px;overflow:hidden;}
@media(max-width:1000px){.block-container{padding:1.5rem;}.basis-hero-art{width:150px;flex-basis:150px;}.basis-hero{padding:27px;}}
@media(max-width:700px){.basis-hero-art{display:none;}.basis-hero{padding:24px;}.basis-hero h1{font-size:28px;}.basis-phase-plan{grid-template-columns:1fr;}.basis-phase{border-right:0;border-bottom:1px solid #e3e9ed;}.basis-breadcrumb{font-size:10px;}}
</style>
"""

# Decorative evidence rings, not a representation of sample measurements.
ART = '''<svg viewBox="0 0 220 180" fill="none" aria-hidden="true" focusable="false"><circle cx="110" cy="90" r="75" stroke="#77b7bd" stroke-opacity=".22"/><circle cx="110" cy="90" r="58" stroke="#9fd7d5" stroke-opacity=".2"/><circle cx="110" cy="90" r="41" stroke="#d6b778" stroke-opacity=".5" stroke-dasharray="2 8"/><path d="M62 36C150 25 63 151 157 145M157 36C67 26 151 150 62 145" stroke="#a4ded4" stroke-width="2.4"/><path d="M73 42h72M85 58h48M95 74h28M95 108h28M85 124h48M73 140h72" stroke="#a4ded4" stroke-opacity=".5"/><circle cx="62" cy="36" r="5" fill="#d5b479"/><circle cx="157" cy="145" r="5" fill="#a4ded4"/><circle cx="177" cy="56" r="3" fill="#d5b479"/><circle cx="43" cy="118" r="3" fill="#a4ded4"/></svg>'''


def hero(title, description, version):
    return ('<div class="basis-hero"><div><div class="basis-eyebrow">Bactromics · Genomic evidence workspace</div>'
            f'<h1>{escape(title)}</h1><p>{escape(description)}</p>'
            f'<div class="basis-hero-meta"><span><strong>BaSIS {escape(version)}</strong></span>'
            '<span>Snakemake orchestration</span><span>Local analysis · traceable evidence</span></div></div>'
            f'<div class="basis-hero-art">{ART}</div></div>')


def phase_plan():
    return '''<div class="basis-phase-plan" aria-label="Workflow sequence"><div class="basis-phase"><small>01 · Prepare</small><strong>Tools &amp; resources</strong><p>Install and verify everything requested before sample analysis.</p></div><div class="basis-phase"><small>02 · Analyse</small><strong>Safety · DeepSafe · Extend</strong><p>Snakemake schedules dependencies and resumes valid completed work.</p></div><div class="basis-phase"><small>03 · Explore</small><strong>One evidence report</strong><p>Open findings, motifs, trees, native results and provenance.</p></div></div>'''


def study_configurations(workspace, jobs):
    """Discover shallow study configs and recorded source paths without traversal."""
    root = Path(workspace)
    paths = set()
    for pattern in ("config.yaml", "*/config.yaml", "studies/*/config.yaml"):
        paths.update(p.resolve() for p in root.glob(pattern) if p.is_file())
    for job in jobs:
        source = job.get("request", {}).get("config")
        if source and job.get("kind") in {"run", "doctor", "audit"}:
            path = Path(source).expanduser()
            if path.is_file():
                paths.add(path.resolve())
        if job.get("kind") in {"import", "init"} and job.get("outdir"):
            path = Path(job["outdir"]) / "config.yaml"
            if path.is_file():
                paths.add(path.resolve())
    return sorted(map(str, paths))


def state_badge(state):
    tone = "complete" if state == "completed" else "failed" if state == "failed" else "attention" if state in {"incomplete", "needs_attention", "interrupted", "orphaned_active"} else ""
    return f'<span class="basis-state {tone}">{escape(str(state).replace("_", " "))}</span>'
