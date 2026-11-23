"""Render a ``ConsoleView`` as one self-contained HTML page (no script, no network, inline SVG).

The renderer reads traced values from the view and formats them with ``causelens.ui.format``; it never calls a tool, reads the log for a
number, or computes one. Chart geometry (positions, widths) is derived from the traced values, but no axis tick or derived figure is
printed: every number on the page is a traced value, a call number linking to the provenance table, or a step number. Every piece of
text is escaped. Colour is never the only carrier of meaning (verdicts, strengths and statuses are written out).
"""
from __future__ import annotations

from datetime import date
from html import escape

from causelens.agents.result import (
    CandidateFinding,
    CausalEstimate,
    ExperimentRecommendation,
    Unexplained,
)
from causelens.agents.trace import Traced, TracedInterval
from causelens.ui import wording
from causelens.ui.format import exact, fmt_bounds, fmt_traced
from causelens.ui.view import (
    ConsoleView,
    EstimateBlock,
    Fact,
    PlaceboPanel,
    StepView,
    TimelinePanel,
    WaterfallPanel,
)

CSS = """
:root{--ink:#1d2330;--mute:#556070;--line:#d5dae3;--bg:#f6f7fa;--card:#fff;--neg:#b3261e;--pos:#1b5fa8;--warn:#8a5a00;--ok:#1d6b3a}
*{box-sizing:border-box}body{margin:0;font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:var(--ink);background:var(--bg)}
a{color:var(--pos)}header,main,footer{max-width:1040px;margin:0 auto;padding:16px 20px}
h1{font-size:1.5rem;margin:.2rem 0}h2{font-size:1.25rem;margin:0 0 .4rem}h3{font-size:1.05rem;margin:.8rem 0 .3rem}
section.step{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px 18px;margin:16px 0}
.intro,.caveat,.mute{color:var(--mute)}.caveat{font-size:.9rem}
nav ol{display:flex;flex-wrap:wrap;gap:8px;list-style:none;padding:0;margin:8px 0}
nav li a{display:block;padding:6px 10px;border:1px solid var(--line);border-radius:6px;background:var(--card);text-decoration:none;color:var(--ink)}
.chip{display:inline-block;padding:1px 8px;border-radius:10px;border:1px solid currentColor;font-size:.85rem;font-weight:600;white-space:nowrap}
.s-strong{color:var(--ok)}.s-moderate{color:var(--warn)}.s-weak{color:#9a3d00}.s-insufficient{color:var(--neg)}
.v-supported{color:var(--ok)}.v-rejected{color:var(--neg)}.v-inconclusive{color:var(--warn)}
.st-done{color:var(--ok)}.st-stopped{color:var(--neg)}.st-skipped,.st-not_run{color:var(--mute)}
.banner{border:1px solid var(--warn);background:#fff7e6;border-radius:8px;padding:10px 14px;margin:12px 0}
.banner.stop{border-color:var(--neg);background:#fdecea}
.strength{border:2px solid currentColor;border-radius:8px;padding:10px 14px;margin:12px 0;background:var(--card)}
dl.facts{display:grid;grid-template-columns:minmax(220px,max-content) 1fr;gap:4px 16px;margin:.5rem 0}dl.facts dt{color:var(--mute)}dl.facts dd{margin:0}
.num{font-variant-numeric:tabular-nums}sup.src{font-size:.7rem;margin-left:1px}sup.src a{text-decoration:none;color:var(--mute)}
blockquote.tool{margin:.5rem 0;padding:.3rem .8rem;border-left:3px solid var(--line);color:var(--ink)}
blockquote.said{border-left-color:var(--pos)}blockquote.claim{border-left-color:var(--warn);background:#fffaf0}
.card{border:1px solid var(--line);border-radius:8px;padding:10px 14px;margin:10px 0}
table{border-collapse:collapse;width:100%;font-size:.92rem;margin:.4rem 0}th,td{border-bottom:1px solid var(--line);padding:4px 8px;text-align:left;vertical-align:top}
th{color:var(--mute);font-weight:600}svg{max-width:100%;height:auto;display:block;margin:.4rem 0}svg text{font:12px system-ui,sans-serif;fill:var(--ink)}
svg .axis{stroke:#8b94a3;stroke-width:1}svg .zero{stroke:var(--ink);stroke-dasharray:4 3;stroke-width:1}
ul.plain{margin:.3rem 0;padding-left:1.2rem}details{margin:.4rem 0}summary{cursor:pointer}
code,dd,li,blockquote,p{overflow-wrap:anywhere}
@media (max-width:640px){header,main,footer{padding:10px 12px}section.step{padding:12px}dl.facts{grid-template-columns:1fr;gap:0}
dl.facts dd{margin-bottom:.5rem}table{display:block;overflow-x:auto;max-width:100%}h1{font-size:1.25rem}nav ol{gap:6px}}
"""


def e(text: object) -> str:
    return escape(str(text), quote=True)


# --------------------------------------------------------------------------- traced values

def src(call: int) -> str:
    return f'<sup class="src"><a href="#call-{call}" aria-label="source: tool call {call}">{call}</a></sup>'


def num(t: Traced) -> str:
    """A traced value, its call number as a link to the provenance table, and the exact value in a tooltip."""
    return f'<span class="num" title="{e("exact: " + exact(t.value) + "; " + t.path)}">{e(fmt_traced(t))}</span>{src(t.call)}'


def interval(i: TracedInterval, *, show_n: bool = True) -> str:
    est, lo, hi, level = fmt_bounds(i)
    tip = e(f"exact: {i.estimate!r} [{i.lower!r}, {i.upper!r}] at level {i.level!r}; {i.path}")
    body = f'<span class="num" title="{tip}">{e(est)}</span>{src(i.call)} <span class="mute">({e(level)} interval {e(lo)} to {e(hi)})</span>'
    if show_n and i.n:
        body += ' <span class="mute">on ' + "; ".join(f"{e(n.label)} {num(n)}" for n in i.n) + "</span>"
    elif show_n:
        body += f' <span class="mute">({e(wording.NO_SAMPLE_SIZE)})</span>'
    return body


def item_html(item: Traced | TracedInterval | None) -> str:
    if item is None:
        return f'<span class="mute">{e(wording.NOT_AVAILABLE)}</span>'
    return interval(item) if isinstance(item, TracedInterval) else num(item)


def chip(kind: str, value: str) -> str:
    return f'<span class="chip {kind}-{e(value)}">{e(value.replace("_", " "))}</span>'


def facts_html(facts: list[Fact]) -> str:
    if not facts:
        return ""
    rows = "".join(f"<dt>{e(f.label)}</dt><dd>{item_html(f.item)}</dd>" for f in facts)
    return f'<dl class="facts">{rows}</dl>'


def tool_text(t: Traced) -> str:
    return f'<blockquote class="tool">{e(t.value)}{src(t.call)}</blockquote>'


# --------------------------------------------------------------------------- charts

W = 760


def _ord(t: Traced) -> int:
    return date.fromisoformat(str(t.value)).toordinal()


def _scale(lo: float, hi: float, width: float, left: float):
    span = (hi - lo) or 1.0
    return lambda v: left + (v - lo) / span * width


def timeline_svg(p: TimelinePanel) -> str:
    dates = [d for d in (p.first, p.last, p.best_date, *p.plausible, *p.excluded) if d is not None]
    dates += [x for ev in p.events for x in (ev.event_date, ev.onset_date)]
    dates += [pt.date for pt in p.points]
    for band in (p.pre, p.post):
        if band:
            dates += [band.start, band.end]
    if not dates:
        return ""
    lo, hi = min(_ord(d) for d in dates), max(_ord(d) for d in dates) + 1
    x = _scale(lo, hi, W - 40, 20)
    top, bottom = 34, 190
    levels = [b.level for b in (p.pre, p.post) if b and b.level]
    daily = [pt for pt in p.points if pt.conversion is not None]
    values = [float(pt.conversion.value) for pt in daily if pt.conversion is not None]  # type: ignore[arg-type]
    values += [v for i in levels for v in (i.lower, i.upper)]
    parts = [f'<line class="axis" x1="20" y1="{bottom}" x2="{W - 20}" y2="{bottom}"/>']
    y = None
    if values:
        vmin, vmax = min(values), max(values)
        pad = (vmax - vmin) * 0.12 or 1.0
        y = _scale(vmin - pad, vmax + pad, -(bottom - top), bottom)  # larger values higher up
    if len(p.plausible) == 2:
        a, b = x(_ord(p.plausible[0])), x(_ord(p.plausible[1]) + 1)
        parts.append(f'<rect x="{a:.1f}" y="{top}" width="{max(b - a, 2):.1f}" height="{bottom - top}" fill="#f4d9a8" opacity=".6"><title>{e(wording.PLAUSIBLE_RANGE)}</title></rect>')
    for band, colour in ((p.pre, "#6b7a90"), (p.post, "#1b5fa8")):
        if band is None or band.level is None or y is None:
            continue
        a, b = x(_ord(band.start)), x(_ord(band.end) + 1)
        lv = band.level
        est, lo_b, hi_b, level = fmt_bounds(lv)
        parts.append(f'<rect x="{a:.1f}" y="{y(lv.upper):.1f}" width="{b - a:.1f}" height="{max(y(lv.lower) - y(lv.upper), 1):.1f}" fill="{colour}" opacity=".18">'
                     f"<title>{e(band.label)} window: {e(est)} ({e(level)} interval {e(lo_b)} to {e(hi_b)})</title></rect>")
        parts.append(f'<line x1="{a:.1f}" x2="{b:.1f}" y1="{y(lv.estimate):.1f}" y2="{y(lv.estimate):.1f}" stroke="{colour}" stroke-width="2" stroke-dasharray="6 3">'
                     f"<title>{e(band.label)} window level {e(est)}</title></line>")
    if y is not None and daily:
        run: list[str] = []
        for pt in p.points:
            if pt.conversion is None or pt.status.value != "ok":
                if len(run) > 1:
                    parts.append(f'<polyline points="{" ".join(run)}" fill="none" stroke="#1d2330" stroke-width="1.2" opacity=".6"/>')
                run = []
                continue
            run.append(f"{x(_ord(pt.date) + 0.5):.1f},{y(float(pt.conversion.value)):.1f}")  # type: ignore[arg-type]
        if len(run) > 1:
            parts.append(f'<polyline points="{" ".join(run)}" fill="none" stroke="#1d2330" stroke-width="1.2" opacity=".6"/>')
        for pt in p.points:
            if pt.conversion is None:
                continue
            cx, cy = x(_ord(pt.date) + 0.5), y(float(pt.conversion.value))  # type: ignore[arg-type]
            tip = (f"{fmt_traced(pt.date)}: {fmt_traced(pt.conversion)} ({fmt_traced(pt.orders)} orders of {fmt_traced(pt.sessions)} sessions), "
                   f"{'excluded day' if pt.status.value == 'excluded' else 'ordinary day'}")
            if pt.status.value == "excluded":
                parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4.5" fill="#fff" stroke="#b3261e" stroke-width="2"><title>{e(tip)}</title></circle>')
            else:
                parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="2.4" fill="#1d2330"><title>{e(tip)}</title></circle>')
        ordinary = [pt for pt in daily if pt.status.value == "ok"]
        if ordinary:
            for pt, anchor, dy in ((max(ordinary, key=lambda q: float(q.conversion.value)), "middle", -8),  # type: ignore[union-attr, arg-type]
                                   (min(ordinary, key=lambda q: float(q.conversion.value)), "middle", 16)):  # type: ignore[union-attr, arg-type]
                parts.append(f'<text x="{x(_ord(pt.date) + 0.5):.1f}" y="{y(float(pt.conversion.value)) + dy:.1f}" text-anchor="{anchor}">'  # type: ignore[union-attr, arg-type]
                             f"{e(fmt_traced(pt.conversion))} on {e(fmt_traced(pt.date))}</text>")  # type: ignore[arg-type]
    elif y is not None:
        for band in (p.pre, p.post):
            if band and band.level:
                parts.append(f'<text x="{(x(_ord(band.start)) + x(_ord(band.end) + 1)) / 2:.1f}" y="{y(band.level.upper) - 6:.1f}" text-anchor="middle">'
                             f"{e(band.label)}: {e(fmt_bounds(band.level)[0])}</text>")
    if p.best_date is not None:
        bx = x(_ord(p.best_date) + 0.5)
        parts.append(f'<line x1="{bx:.1f}" x2="{bx:.1f}" y1="{top - 8}" y2="{bottom}" stroke="#b3261e" stroke-width="2" stroke-dasharray="5 3"><title>change point {e(fmt_traced(p.best_date))}</title></line>')
        parts.append(f'<text x="{bx:.1f}" y="{top - 14}" text-anchor="middle" fill="#b3261e">change point {e(fmt_traced(p.best_date))}</text>')
    for k, ev in enumerate(p.events):
        ex = x(_ord(ev.event_date) + 0.5)
        mark = chr(ord("A") + k)
        parts.append(f'<path d="M{ex - 5:.1f} {bottom + 16} L{ex + 5:.1f} {bottom + 16} L{ex:.1f} {bottom + 5} Z" fill="#555">'
                     f"<title>{mark}: {e(fmt_traced(ev.name))}, {e(fmt_traced(ev.event_date))}, {e(fmt_traced(ev.verdict))}</title></path>")
        parts.append(f'<text x="{ex:.1f}" y="{bottom + 30}" text-anchor="middle">{mark}</text>')
    for d in p.excluded:
        dx = x(_ord(d) + 0.5)
        parts.append(f'<line x1="{dx:.1f}" x2="{dx:.1f}" y1="{bottom - 10}" y2="{bottom}" stroke="#b3261e" stroke-width="3"><title>{e(wording.EXCLUDED_DAY)} {e(fmt_traced(d))}</title></line>')
    if p.first is not None:
        parts.append(f'<text x="20" y="{bottom + 46}">{e(fmt_traced(p.first))}</text>')
    if p.last is not None:
        parts.append(f'<text x="{W - 20}" y="{bottom + 46}" text-anchor="end">{e(fmt_traced(p.last))}</text>')
    desc = "; ".join(f"{b.label} window level {fmt_bounds(b.level)[0]}" for b in (p.pre, p.post) if b and b.level)
    return (f'<svg viewBox="0 0 {W} {bottom + 56}" role="img" aria-labelledby="tl-t tl-d"><title id="tl-t">{e(wording.TIMELINE_TITLE)}</title>'
            f'<desc id="tl-d">{e(desc)}</desc>{"".join(parts)}</svg>')


def waterfall_svg(p: WaterfallPanel) -> str:
    left, label_w, row_h = 20, 250, 26
    cum, bars = 0.0, []
    for r in p.rows:
        v = float(r.contribution.value)  # type: ignore[arg-type]
        bars.append((r, cum, cum + v))
        cum += v
    total = float(p.overall_change.value)  # type: ignore[arg-type]
    edges = [0.0, total, *[b for _, a, b in bars], *[a for _, a, _ in bars]]
    x = _scale(min(edges), max(edges), W - left - label_w - 150, left + label_w)
    height = (len(bars) + 1) * row_h + 20
    parts = [f'<line class="zero" x1="{x(0):.1f}" x2="{x(0):.1f}" y1="4" y2="{height - 16}"/>']
    for k, (r, a, b) in enumerate(bars):
        yy = 8 + k * row_h
        colour = "#b3261e" if float(r.contribution.value) < 0 else "#1b5fa8"  # type: ignore[arg-type]
        stroke = ' stroke="#1d2330" stroke-width="2"' if r.localized else ""
        tip = (f"{fmt_traced(r.label)}: contribution {fmt_traced(r.contribution)}, rate effect {fmt_traced(r.rate_effect)}, "
               f"mix effect {fmt_traced(r.mix_effect)}" + (f", FDR-adjusted q {fmt_traced(r.q_value)}" if r.q_value else ""))
        parts.append(f'<text x="{left}" y="{yy + 14}">{e(fmt_traced(r.label))}{" (localized)" if r.localized else ""}<title>{e(tip)}</title></text>')
        parts.append(f'<rect x="{min(x(a), x(b)):.1f}" y="{yy}" width="{max(abs(x(b) - x(a)), 1.5):.1f}" height="{row_h - 8}" fill="{colour}"{stroke}><title>{e(tip)}</title></rect>')
        parts.append(f'<text x="{max(x(a), x(b)) + 6:.1f}" y="{yy + 14}">{e(fmt_traced(r.contribution))}</text>')
    yy = 8 + len(bars) * row_h
    parts.append(f'<text x="{left}" y="{yy + 14}" font-weight="600">Overall change</text>')
    parts.append(f'<rect x="{min(x(0), x(total)):.1f}" y="{yy}" width="{max(abs(x(total) - x(0)), 1.5):.1f}" height="{row_h - 8}" fill="none" stroke="#1d2330" stroke-width="2">'
                 f"<title>Overall change {e(fmt_traced(p.overall_change))}</title></rect>")
    parts.append(f'<text x="{max(x(0), x(total)) + 6:.1f}" y="{yy + 14}" font-weight="600">{e(fmt_traced(p.overall_change))}</text>')
    return (f'<svg viewBox="0 0 {W} {height}" role="img" aria-labelledby="wf-t wf-d"><title id="wf-t">{e(wording.WATERFALL_TITLE)}</title>'
            f'<desc id="wf-d">{e(wording.WATERFALL_DESC)} {e(fmt_traced(p.partition))}.</desc>{"".join(parts)}</svg>')


def forest_svg(rows: list[tuple[str, TracedInterval]], uid: str, title: str) -> str:
    if not rows:
        return ""
    lows = [i.lower for _, i in rows] + [0.0]
    highs = [i.upper for _, i in rows] + [0.0]
    left, label_w, row_h = 20, 230, 30
    x = _scale(min(lows), max(highs), W - left - label_w - 230, left + label_w)
    height = len(rows) * row_h + 40
    parts = [f'<line class="zero" x1="{x(0):.1f}" x2="{x(0):.1f}" y1="6" y2="{height - 26}"/>',
             f'<text x="{x(0):.1f}" y="{height - 8}" text-anchor="middle">{e(wording.NO_CHANGE)}</text>']
    desc = []
    for k, (label, i) in enumerate(rows):
        yy = 18 + k * row_h
        est, lo, hi, level = fmt_bounds(i)
        tip = f"{label}: {est}, {level} interval {lo} to {hi}"
        parts.append(f'<text x="{left}" y="{yy + 4}">{e(label)}<title>{e(tip)}</title></text>')
        parts.append(f'<line x1="{x(i.lower):.1f}" x2="{x(i.upper):.1f}" y1="{yy}" y2="{yy}" stroke="#1d2330" stroke-width="2"><title>{e(tip)}</title></line>')
        parts.append(f'<circle cx="{x(i.estimate):.1f}" cy="{yy}" r="5" fill="#1b5fa8"><title>{e(tip)}</title></circle>')
        parts.append(f'<text x="{x(i.upper) + 10:.1f}" y="{yy + 4}">{e(est)} [{e(lo)}, {e(hi)}]</text>')
        desc.append(f"{label} {est}, {level} interval {lo} to {hi}")
    return (f'<svg viewBox="0 0 {W} {height}" role="img" aria-labelledby="{uid}-t {uid}-d"><title id="{uid}-t">{e(title)}</title>'
            f'<desc id="{uid}-d">{e("; ".join(desc))}</desc>{"".join(parts)}</svg>')


# --------------------------------------------------------------------------- panels

def timeline_html(p: TimelinePanel) -> str:
    out = [f"<h3>{e(wording.TIMELINE_TITLE)}</h3>", timeline_svg(p)]
    if p.points:
        out.append(f'<p class="mute">{e(wording.DAILY_VALUES_FROM)}{src(p.points[0].date.call)}</p>')
    if p.events:
        rows = "".join(
            f"<tr><td>{chr(ord('A') + k)}</td><td>{num(ev.name)}</td><td>{num(ev.event_date)}</td><td>{num(ev.onset_date)}</td><td>{chip('v', str(ev.verdict.value))}</td></tr>"
            for k, ev in enumerate(p.events))
        out.append(f"<table><thead><tr><th>Mark</th><th>Event</th><th>Event date</th><th>Onset tested</th><th>Verdict</th></tr></thead><tbody>{rows}</tbody></table>")
    bands = "".join(f"<li>{e(b.label)} window {num(b.start)} to {num(b.end)}: {item_html(b.level)}</li>" for b in (p.pre, p.post) if b and b.level)
    if bands:
        out.append(f'<ul class="plain">{bands}</ul>')
    out.append(f'<p class="caveat">{e(p.caveat)} {e(wording.TIMELINE_LEGEND if p.points else wording.TIMELINE_LEGEND_WINDOWS)}</p>')
    return "".join(out)


def waterfall_html(p: WaterfallPanel) -> str:
    rows = "".join(
        f"<tr><td>{num(r.rank)}</td><td>{num(r.label)}{' <strong>(localized)</strong>' if r.localized else ''}</td><td>{num(r.contribution)}</td>"
        f"<td>{num(r.rate_effect)}</td><td>{num(r.mix_effect)}</td><td>{num(r.q_value) if r.q_value else e(wording.NOT_TESTED)}</td></tr>"
        for r in p.rows)
    return (f"<h3>{e(wording.WATERFALL_TITLE)}: {num(p.partition)}</h3>{waterfall_svg(p)}"
            f"<table><thead><tr><th>Rank</th><th>Segment</th><th>Contribution</th><th>Rate effect</th><th>Mix effect</th><th>FDR-adjusted q</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
            f'<p class="caveat">Segments in this partition: {num(p.n_segments)}. Residual of the decomposition: {num(p.residual)}. {e(p.caveat)}</p>')


def placebo_html(p: PlaceboPanel, uid: str) -> str:
    rows = [(f"placebo {fmt_traced(r.fake_date)}", r.effect) for r in p.rows]
    if p.real_effect is not None:
        rows.append(("the estimate being checked", p.real_effect))
    table = "".join(f"<tr><td>{num(r.fake_date)}</td><td>{interval(r.effect, show_n=False)}</td><td>{num(r.p_holm)}</td></tr>" for r in p.rows)
    return (f"<h3>{e(wording.PLACEBO_TITLE)}: {chip('st', str(p.status.value))}{src(p.status.call)}</h3>"
            f"{forest_svg(rows, uid, wording.PLACEBO_TITLE)}"
            f"<table><thead><tr><th>Fake date</th><th>Effect</th><th>Holm-adjusted p</th></tr></thead><tbody>{table}</tbody></table>"
            f'<p class="caveat">Placebo dates evaluated: {num(p.n_evaluated)}. Criterion: {num(p.criterion)} {e(p.caveat)}</p>')


def estimate_html(block: EstimateBlock, index: int) -> str:
    est: CausalEstimate = block.estimate
    rows = [("Differential effect on the focal segment", est.effect_pp)]
    if est.overall_pp is not None:
        rows.append(("Effect on the overall rate", est.overall_pp))
    facts = [Fact(label="Candidate event", item=est.event), Fact(label="Onset date tested", item=est.onset_date),
             Fact(label="Focal segment", item=est.focal), Fact(label="Comparison", item=est.comparison),
             Fact(label="Differential effect (difference-in-differences)", item=est.effect_pp),
             Fact(label="Effect on the overall conversion rate", item=est.overall_pp),
             Fact(label="Lost orders in the post-period", item=est.lost_orders),
             Fact(label="Pre-trend check", item=est.pre_trend_status), Fact(label="Pre-trend slope difference", item=est.pre_trend_slope_pp_per_day),
             Fact(label="Pre-trend p-value (read with the slope)", item=est.pre_trend_p_value), Fact(label="Placebo check", item=est.placebo_status)]
    chart = forest_svg([(label, i) for label, i in rows if i is not None], f"did{index}", wording.DID_TITLE)
    claim = est.claim
    claim_html = (f'<p><strong>Causal claim: {"allowed" if claim.allowed else "not made"}.</strong> {e(claim.reason)}. {e(est.attribution_label)}</p>')
    assumptions = "".join(f"<li>{num(a)}</li>" for a in est.assumptions)
    return (f'<div class="card"><h3>{e(wording.DID_TITLE)}: {num(est.event)}</h3>{facts_html([f for f in facts if f.item is not None])}{chart}'
            f"{claim_html}<h3>Assumptions this estimate rests on</h3><ul class=\"plain\">{assumptions}</ul>"
            f"{placebo_html(block.placebo, f'pl{index}') if block.placebo else ''}</div>")


def candidate_html(c: CandidateFinding) -> str:
    lags = "".join(f"<tr><td>{num(v.lag_days)}</td><td>{num(v.onset_date)}</td><td>{chip('v', str(v.verdict.value))}</td><td>{num(v.headline)}</td></tr>" for v in c.lag_sensitivity)
    grounds = "".join(f"<li>{e(g.label)}: {num(g)}</li>" for g in c.rejection_grounds)
    share = ""
    if c.share_confidence_set is not None:
        s = c.share_confidence_set
        if s.available.value:
            pieces = " or ".join(
                f"{num(p.lower) if p.lower else e(wording.NO_LOWER_BOUND)} to {num(p.upper) if p.upper else e(wording.NO_UPPER_BOUND)}" for p in s.pieces)
            share = f"<p>Share of the localized change that begins at this onset ({num(s.level)} confidence set, {num(s.kind) if s.kind else ''}): {pieces}. <span class=\"caveat\">{e(s.note)}</span></p>"
        else:
            share = f"<p>Share confidence set unavailable: {num(s.reason) if s.reason else ''}</p>"
    reasons = "".join(f"<li>{num(r)}</li>" for r in c.reasons)
    return (f'<div class="card"><h3>{num(c.event)} {chip("v", str(c.verdict.value))}</h3><p>{num(c.headline)}</p><p class="mute">Hypothesis tested: {num(c.hypothesis)}</p>'
            f'<p><strong>Why it is kept or set aside.</strong> {e(c.why)}</p>'
            f'{"<p>Rejected on:</p><ul class=plain>" + grounds + "</ul>" if grounds else ""}<ul class="plain">{reasons}</ul>'
            f"<p>Evaluated lag {num(c.lag_days)}; onset {num(c.onset_date)}. Release attribution ambiguous: {num(c.attribution_ambiguous)}. {num(c.attribution_note)}</p>"
            f'{f"<p>Evidence strength of this candidate: {num(c.evidence_strength)}</p>" if c.evidence_strength else ""}{share}'
            f"<table><thead><tr><th>Lag</th><th>Onset</th><th>Verdict</th><th>Headline</th></tr></thead><tbody>{lags}</tbody></table></div>")


def unexplained_html(u: Unexplained) -> str:
    facts = [Fact(label="Remaining share of the change", item=u.remaining_share_of_change),
             Fact(label="Rate effect not distinguishable from noise", item=u.rate_effect_not_distinguishable_pp)]
    return f"<h3>{e(wording.UNEXPLAINED_TITLE)}</h3>{facts_html([f for f in facts if f.item is not None])}<p class=\"caveat\">{e(u.note)}</p>"


def experiment_html(x: ExperimentRecommendation) -> str:
    facts = [Fact(label="Status", item=x.status), Fact(label="Purpose", item=x.purpose), Fact(label="Hypothesis", item=x.hypothesis),
             Fact(label="Intervention", item=x.intervention), Fact(label="Randomization unit", item=x.randomization_unit),
             Fact(label="Primary metric", item=x.primary_metric), Fact(label="Minimum detectable effect", item=x.mde_pp),
             Fact(label="Sessions per arm (control)", item=x.n_per_arm_control), Fact(label="Expected duration (days)", item=x.duration_days),
             Fact(label="Achieved power (normal approximation)", item=x.achieved_power)]
    arms = "".join(f"<tr><td>{num(a.name)}</td><td>{num(a.role)}</td><td>{num(a.n_sessions)}</td><td>{num(a.allocation_share)}</td></tr>" for a in x.arms)
    guard = "".join(f"<li><strong>{num(name)}</strong>: {num(detail)}</li>" for name, detail in zip(x.guardrails[::2], x.guardrails[1::2], strict=False))
    warn = "".join(f"<li>{num(w)}</li>" for w in x.warnings)
    needs = "".join(f"<li>{num(n)}</li>" for n in x.needs)
    return (facts_html([f for f in facts if f.item is not None])
            + (f"<table><thead><tr><th>Arm</th><th>Role</th><th>Sessions</th><th>Allocation</th></tr></thead><tbody>{arms}</tbody></table>" if arms else "")
            + (f"<h3>Guardrails</h3><ul class=plain>{guard}</ul>" if guard else "")
            + (f'<h3>Warnings from the design tool</h3><ul class="plain">{warn}</ul>' if warn else "")
            + (f"<h3>What is missing</h3><ul class=plain>{needs}</ul>" if needs else "") + tool_text(x.summary))


# --------------------------------------------------------------------------- page

def step_html(s: StepView) -> str:
    heading = f'<h2 id="h-{s.key}">Step {s.number}: {e(s.title.split(": ", 1)[1])} {chip("st", s.status)}</h2>'
    out = [f'<section class="step" id="step-{s.key}" aria-labelledby="h-{s.key}">{heading}<p class="intro">{e(s.intro)}</p>']
    if s.status == "not_run":
        out.append(f'<p class="mute">{e(wording.STEP_NOT_RUN)}</p>')
    out += [f'<p class="mute">{e(n)}</p>' for n in s.stage_notes]
    out.append(facts_html(s.facts))
    out += [tool_text(t) for t in s.texts]
    if s.timeline is not None:
        out.append(timeline_html(s.timeline))
    if s.waterfall is not None:
        out.append(waterfall_html(s.waterfall))
    if s.unexplained is not None:
        out.append(unexplained_html(s.unexplained))
    out += [candidate_html(c) for c in s.candidates]
    if s.key == "test" and s.status != "not_run":
        if s.alternatives:
            out.append(f'<h3>{e(wording.ALTERNATIVES_TITLE)}</h3><ul class="plain">' + "".join(f"<li>{num(a)}</li>" for a in s.alternatives) + "</ul>")
        elif not s.candidates:
            out.append(f'<p class="mute">{e(wording.NO_CANDIDATES_TESTED)}</p>')
    out += [estimate_html(b, i) for i, b in enumerate(s.estimates)]
    if s.key == "estimate" and not s.estimates and s.estimate_note:
        out.append(f'<div class="banner"><strong>No causal estimate.</strong> {e(s.estimate_note)}</div>')
    if s.experiment is not None:
        out.append(experiment_html(s.experiment))
    elif s.key == "recommend" and s.experiment_note:
        out.append(f'<div class="banner"><strong>No experiment designed.</strong> {e(s.experiment_note)}</div>')
    if s.statements:
        out.append(f"<h3>{e(wording.STATEMENTS_TITLE)}</h3>" + "".join(
            f'<blockquote class="tool {"claim" if "difference-in-differences" in t else "said"}">{e(t)}</blockquote>' for t in s.statements))
    out.append("</section>")
    return "".join(out)


def header_html(v: ConsoleView) -> str:
    r = v.result
    strength = r.evidence
    base = f" (base assessment: {e(strength.base_strength)}; see the reasons)" if strength.base_strength and strength.base_strength != strength.strength else ""
    reasons = "".join(f"<li>{num(x)}</li>" for x in strength.reasons)
    parts = [f"<h1>{e(r.question)}</h1>",
             f'<p>Run status: {chip("st", "done" if r.status == "completed" else "stopped")} <span class="mute">{e(r.status.replace("_", " "))}</span></p>']
    if r.status != "completed":
        parts.append(f'<div class="banner stop"><strong>The investigation stopped</strong>{" at the " + e(r.stopped_stage) + " stage" if r.stopped_stage else ""}. '
                     f"{e(r.stopped_reason or '')}</div>")
    parts.append(f'<div class="strength s-{e(strength.strength)}" role="group" aria-label="Evidence strength"><strong>Evidence strength: {e(strength.strength)}</strong>{base}'
                 f'<p>{e(strength.reason)}</p>{f"<ul class=plain>{reasons}</ul>" if reasons else ""}</div>')
    d = r.dataset
    if d is not None:
        events = f"<br>events file {num(d.events_sha256)}" if d.events_sha256 else ""
        parts.append(f'<p class="mute">{e(wording.DATA_TITLE)}: {num(d.id)}, {num(d.n_rows)} rows of daily metrics, {num(d.first_date)} to {num(d.last_date)}.'
                     f"<details><summary>{e(wording.FINGERPRINTS)}</summary><code>metrics file {num(d.metrics_sha256)}{events}</code></details></p>")
    dq = r.data_quality
    if dq is not None and (dq.excluded_dates or dq.flags):
        dates = ", ".join(num(d) for d in dq.excluded_dates) or e(wording.NONE)
        flags = "".join(f"<li>{num(f.date)} {num(f.segment)}: {num(f.reason)}" + (f" Linked incident: {num(f.linked_event)}." if f.linked_event else "") + "</li>" for f in dq.flags)
        parts.append(f'<div class="banner"><strong>Data-quality warning.</strong> Days excluded from the estimates: {dates}. {e(dq.note)} '
                     f"{num(dq.exclusion_statement)}<details><summary>Flags raised</summary><ul class=plain>{flags}</ul></details></div>")
    return "".join(parts)


def footer_html(v: ConsoleView) -> str:
    r = v.result
    rows = "".join(
        f'<tr id="call-{c.seq}"><td>{c.seq}</td><td>{e(c.role)}</td><td>{e(c.stage)}</td><td><code>{e(c.tool)}</code></td>'
        f'<td>{"failed: " + e(c.error or "") if c.failed else "ok"}</td><td>{e(", ".join(c.prior_arguments)) or "none"}; {e(", ".join(c.argument_names))}</td></tr>'
        for c in v.calls)
    assumptions = "".join(f"<li>{num(a)}</li>" for a in r.assumptions)
    choices = "".join(f"<li><code>{e(n.tool)}</code>: {e(', '.join(n.names))}: {e(n.rationale)} ({'used' if n.accepted else 'refused: ' + e(n.problem or '')})</li>" for n in r.parameter_choices)
    notes = "".join(f"<li>{e(n)}</li>" for n in v.notes)
    return (f'<footer><h2>{e(wording.PROVENANCE_TITLE)}</h2><p class="caveat">{e(wording.PROVENANCE_NOTE)}</p>'
            f"<table><thead><tr><th>Call</th><th>Role</th><th>Stage</th><th>Tool</th><th>Result</th><th>Arguments (earlier results, then names)</th></tr></thead><tbody>{rows}</tbody></table>"
            f"{f'<h3>Parameter choices by the reasoner</h3><ul class=plain>{choices}</ul>' if choices else ''}"
            f"{f'<h3>Notes</h3><ul class=plain>{notes}</ul>' if notes else ''}"
            f'<details><summary>All assumptions stated by the tools</summary><ul class="plain">{assumptions}</ul></details>'
            f'<p class="caveat">{e(wording.REASONER_NOTE)}</p></footer>')


def render_html(view: ConsoleView) -> str:
    nav = "".join(f'<li><a href="#step-{s.key}">Step {s.number}: {e(s.title.split(": ", 1)[1])} <span class="chip st-{s.status}">{e(s.status.replace("_", " "))}</span></a></li>'
                  for s in view.steps)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{e(wording.PAGE_TITLE)}</title><style>{CSS}</style></head><body>"
        f'<header>{header_html(view)}<nav aria-label="Investigation steps"><ol>{nav}</ol></nav></header>'
        f'<main>{"".join(step_html(s) for s in view.steps)}</main>{footer_html(view)}</body></html>'
    )

