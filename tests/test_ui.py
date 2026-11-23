"""Task 10: the investigation console (Requirement 8.3, and 8.2 and 8.4 for what it shows).

The console is checked on real investigations through the real MCP server: the reference run, a run that stops at the first step, a run
with no supported candidate, a dataset without an event log, and a dataset whose event name is hostile HTML. The invariants are about
what a reader sees: every number traces to a logged call, uncertainty and evidence strength are never dropped, alternatives and
data-quality warnings stay visible, and the page carries no script and no unescaped text.
"""
from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import ClassVar

import pytest
from agents_support import request, run, write_frames
from hypothesis import given
from hypothesis import strategies as st
from mcp_reference import reference_files
from mcp_support import RunningServer
from scenarios import scenario_data

import causelens.ui.__main__ as cli
from causelens.agents import (
    Investigation,
    McpToolClient,
    TraceError,
    investigate,
    verify_traces,
)
from causelens.agents.trace import CAUSAL_WORDS, Traced, TracedInterval, iter_traced
from causelens.eval.runner import dataset_files
from causelens.mcp import DatasetConfig, DatasetRegistry
from causelens.sim.generate import generate
from causelens.ui import STEP_KEYS, build_view, render_html, wording
from causelens.ui import render as render_module
from causelens.ui import view as view_module
from causelens.ui.format import fmt_bounds, fmt_traced
from causelens.ui.render import forest_svg, timeline_svg
from causelens.ui.view import MAX_WATERFALL_ROWS, TimelinePanel

ROOT = Path(__file__).resolve().parents[1]
CHECKOUT, HERO = "Checkout v4.2", "Homepage hero refresh"


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    return reference_files(tmp_path_factory)


@pytest.fixture(scope="module")
def reference_run(files) -> Investigation:
    return run(*files)


@pytest.fixture(scope="module")
def reference_view(reference_run):
    return build_view(reference_run)


@pytest.fixture(scope="module")
def reference_page(reference_view) -> str:
    return render_html(reference_view)


class Text(HTMLParser):
    """The text a reader sees (and the accessible titles and descriptions), plus the tags and attributes."""

    VOID: ClassVar[set[str]] = {"meta", "br", "hr", "img", "input", "link", "line", "rect", "circle", "path"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.tags: list[str] = []
        self.attrs: list[tuple[str, str, str | None]] = []
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.sources: list[str] = []
        self._skip = 0
        self._in_src = 0

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs += [(tag, k, v) for k, v in attrs]
        if tag == "style":
            self._skip += 1
        if tag == "sup" and ("class", "src") in attrs:
            self._in_src += 1
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs += [(tag, k, v) for k, v in attrs]

    def handle_endtag(self, tag):
        if tag == "style":
            self._skip -= 1
        if tag == "sup" and self._in_src:
            self._in_src -= 1
        if tag in self.VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unexpected </{tag}> with {self.stack[-3:]}")
        else:
            self.stack.pop()

    def handle_data(self, data):
        if self._skip or not data.strip():
            return
        (self.sources if self._in_src else self.text).append(data)


def parse(page: str) -> Text:
    p = Text()
    p.feed(page)
    p.close()
    return p


NUMBER = re.compile(r"\d+(?:[.,]\d+)*(?:e-?\d+)?")


def tokens(text: str) -> set[str]:
    out: set[str] = set()
    for m in NUMBER.findall(text):
        out.add(m)
        out.update(NUMBER.findall(m.replace(",", " ").replace(".", " ")))
    return out


def allowed_tokens(view, state) -> set[str]:
    """Every number token the page may show: formatted traced values, tool text, validated statements, call and step numbers."""
    allowed: set[str] = {str(i) for i in range(8)} | {str(c.seq) for c in state.log}
    for item in iter_traced(view):
        if isinstance(item, Traced):
            allowed |= tokens(fmt_traced(item)) | tokens(str(item.value))
        else:
            allowed |= tokens(" ".join(fmt_bounds(item)))
    for step in view.steps:
        for s in step.statements:
            allowed |= tokens(s)
    for note in view.notes:
        allowed |= tokens(note)
    return allowed


# --------------------------------------------------------------------------- the six steps

def test_the_console_has_the_six_steps_in_order(reference_view):
    assert [s.key for s in reference_view.steps] == list(STEP_KEYS) == ["detect", "locate", "localize", "test", "estimate", "recommend"]
    assert [s.number for s in reference_view.steps] == [1, 2, 3, 4, 5, 6]
    assert all(s.status == "done" for s in reference_view.steps)


def test_each_step_lists_the_calls_that_produced_its_evidence(reference_view, reference_run):
    by_step = {s.key: s.calls for s in reference_view.steps}
    tools = {k: [reference_run.state.call(c).tool for c in calls] for k, calls in by_step.items()}
    assert tools == {
        "detect": ["list_datasets", "check_data_quality", "confirm_change"], "locate": ["detect_change_point", "match_event_candidates", "daily_series"],
        "localize": ["decompose_change"], "test": ["test_candidates"],
        "estimate": ["estimate_did", "check_pre_trends", "run_placebo", "project_impact"], "recommend": ["design_experiment"],
    }
    assert sorted(c for calls in by_step.values() for c in calls) == [c.seq for c in reference_run.state.log]


def test_every_traced_number_in_the_view_is_reread_from_the_log(reference_view, reference_run):
    assert verify_traces(reference_view, reference_run.state) > 250
    forged = next(i for i in iter_traced(reference_view) if isinstance(i, TracedInterval))
    with pytest.raises(TraceError):
        verify_traces(forged.model_copy(update={"estimate": forged.estimate + 1.0}), reference_run.state)


# --------------------------------------------------------------------------- evidence panels

def test_the_timeline_panel_shows_the_window_levels_the_change_point_events_and_excluded_days(reference_view, reference_run):
    p = next(s for s in reference_view.steps if s.key == "locate").timeline
    confirm = reference_run.state.last("confirm_change").result
    assert (p.pre.level.estimate, p.post.level.estimate) == (confirm["pre"]["conversion"]["estimate"], confirm["post"]["conversion"]["estimate"])
    assert (p.pre.level.lower, p.pre.level.upper) == (confirm["pre"]["conversion"]["lower"], confirm["pre"]["conversion"]["upper"])
    assert p.best_date.value == "2026-10-15" and [d.value for d in p.plausible] == ["2026-10-14", "2026-10-15"]
    assert [d.value for d in p.excluded] == ["2026-09-20"]
    assert {(e.name.value, e.verdict.value) for e in p.events} == {(CHECKOUT, "supported"), (HERO, "rejected")}
    assert p.caveat == "" and len(p.points) == 92  # a daily series, one point per day


def test_the_waterfall_panel_is_the_localized_partition_with_the_tools_own_contributions(reference_view, reference_run):
    w = next(s for s in reference_view.steps if s.key == "localize").waterfall
    doc = reference_run.state.last("decompose_change").result
    part = next(p for p in doc["partitions"] if p["name"] == w.partition.value)
    assert w.partition.value == "device × browser" and w.overall_change.value == doc["change_pp"] and w.residual.value == part["residual_pp"]
    ranked = sorted(part["segments"], key=lambda s: s["rank"])[:MAX_WATERFALL_ROWS]
    assert [r.label.value for r in w.rows] == [s["label"] for s in ranked]
    assert [r.contribution.value for r in w.rows] == [s["contribution_pp"] for s in ranked]
    assert [r.rate_effect.value for r in w.rows] == [s["rate_effect_pp"] for s in ranked]
    assert [r.label.value for r in w.rows if r.localized] == ["device=mobile, browser=safari"]
    first = w.rows[0]
    assert first.q_value.value < 0.05 and first.significant.value is True  # the FDR-adjusted q is shown, not a raw p
    assert "not added across partitions" in w.caveat


def test_the_did_and_placebo_panels_carry_intervals_samples_and_assumptions(reference_view, reference_run):
    block = next(s for s in reference_view.steps if s.key == "estimate").estimates[0]
    e, placebo = block.estimate, block.placebo
    did = reference_run.state.last("estimate_did").result
    assert e.effect_pp.estimate == did["effect_pp"]["estimate"] and (e.effect_pp.lower, e.effect_pp.upper) == (did["effect_pp"]["lower"], did["effect_pp"]["upper"])
    assert [n.value for n in e.effect_pp.n][:1] == [did["n_obs"]] and e.assumptions and e.pre_trend_status.value == "pass"
    plc = reference_run.state.last("run_placebo").result["placebos"]
    assert [r.effect.estimate for r in placebo.rows] == [p["effect_pp"]["estimate"] for p in plc]
    assert [r.p_holm.value for r in placebo.rows] == [p["p_value_holm"] for p in plc]
    assert placebo.real_effect == e.effect_pp and placebo.status.value == "pass" and "sample size per placebo" in placebo.caveat


def test_alternatives_rejection_grounds_and_share_sets_survive_into_the_test_step(reference_view, reference_run):
    step = next(s for s in reference_view.steps if s.key == "test")
    assert len(step.alternatives) >= 2 and [a.value for a in step.alternatives] == [a.value for a in reference_run.result.alternatives_considered]
    hero = next(c for c in step.candidates if c.event.value == HERO)
    assert hero.rejection_grounds and hero.share_confidence_set.available.value is True and hero.lag_sensitivity
    assert "does not show that the event contributed nothing" in hero.why


# --------------------------------------------------------------------------- the page

def test_the_page_is_well_formed_and_self_contained(reference_page):
    p = parse(reference_page)
    assert p.errors == [] and p.stack == []
    assert "script" not in p.tags and "iframe" not in p.tags and "link" not in p.tags and "img" not in p.tags
    for tag, name, value in p.attrs:
        assert not name.startswith("on"), (tag, name)
        if name in ("href", "src"):
            assert value.startswith("#"), (tag, name, value)  # no external resource and no external link
    csp = next(v for t, n, v in p.attrs if t == "meta" and n == "content" and "default-src" in (v or ""))
    assert "default-src 'none'" in csp and "script-src" not in csp


def test_each_guardrail_is_shown_with_its_detail(reference_page, reference_run):
    visible = " ".join(parse(reference_page).text)
    pairs = reference_run.result.recommended_experiment.guardrails
    assert pairs and len(pairs) % 2 == 0
    for name, detail in zip(pairs[::2], pairs[1::2], strict=True):
        assert re.search(re.escape(str(name.value)) + r"\s*\d*\s*:\s*" + re.escape(str(detail.value)[:40]), visible)


def test_nothing_on_the_page_is_hidden(reference_page):
    p = parse(reference_page)
    assert not [(t, n, v) for t, n, v in p.attrs if n in ("hidden", "aria-hidden", "style")]
    assert "display:none" not in reference_page.replace(" ", "") and "visibility:hidden" not in reference_page.replace(" ", "")


def test_validated_statements_sit_under_the_step_they_are_about(reference_view):
    by = {s.key: " ".join(s.statements) for s in reference_view.steps}
    assert "excluded from the estimates" in by["detect"] and "Conversion moved by" in by["detect"]
    assert "most plausibly began" in by["locate"] and "coincide in time" in by["locate"]
    assert "concentrated in" in by["localize"] and "concentrated in" not in by["detect"]
    assert "Alternative considered" in by["test"] and "supported under the immediate-effect" in by["test"]
    assert "By difference-in-differences" in by["estimate"] and "By difference-in-differences" not in by["test"]
    assert "Recommended experiment" in by["recommend"]


def test_the_waterfall_draws_only_the_highest_ranked_segments(monkeypatch, reference_run):
    monkeypatch.setattr(view_module, "MAX_WATERFALL_ROWS", 3)
    w = next(s for s in build_view(reference_run).steps if s.key == "localize").waterfall
    assert [r.rank.value for r in w.rows] == [1, 2, 3]


def series_svg(page: str) -> str:
    start = page.index('aria-labelledby="tl-t tl-d"')
    return page[start : page.index("</svg>", start)]


def test_the_series_panel_draws_every_logged_day(reference_view, reference_run, reference_page):
    panel = next(s for s in reference_view.steps if s.key == "locate").timeline
    logged = reference_run.state.last("daily_series").result["points"]
    assert len(panel.points) == len(logged) == 92
    assert [p.date.value for p in panel.points] == [q["date"] for q in logged]
    assert [p.conversion.value for p in panel.points] == [q["conversion"] for q in logged]
    assert [(p.sessions.value, p.orders.value) for p in panel.points] == [(q["sessions"], q["orders"]) for q in logged]
    svg = series_svg(reference_page)
    assert svg.count('r="2.4"') == 91 and svg.count('r="4.5"') == 1  # 91 ordinary days and the one excluded day
    assert '<circle cx=' in svg and 'stroke="#b3261e" stroke-width="2"><title>2026-09-20:' in svg
    assert svg.count("<polyline") == 2  # the excluded day breaks the line
    vertices = sum(len(m.split()) for m in re.findall(r'<polyline points="([^"]+)"', svg))
    assert vertices == 91


def test_every_day_has_a_tooltip_with_its_recorded_counts(reference_view, reference_page):
    panel = next(s for s in reference_view.steps if s.key == "locate").timeline
    svg = series_svg(reference_page)
    for p in panel.points[:5] + [q for q in panel.points if q.status.value == "excluded"]:
        assert f"{fmt_traced(p.date)}: {fmt_traced(p.conversion)} ({fmt_traced(p.orders)} orders of {fmt_traced(p.sessions)} sessions)" in svg
    assert "excluded day" in svg


def test_the_highest_and_lowest_ordinary_days_are_labelled_with_their_logged_values(reference_view, reference_page):
    panel = next(s for s in reference_view.steps if s.key == "locate").timeline
    ordinary = [p for p in panel.points if p.status.value == "ok"]
    hi, lo = max(ordinary, key=lambda p: p.conversion.value), min(ordinary, key=lambda p: p.conversion.value)
    svg = series_svg(reference_page)
    assert f"{fmt_traced(hi.conversion)} on {fmt_traced(hi.date)}" in svg and f"{fmt_traced(lo.conversion)} on {fmt_traced(lo.date)}" in svg
    assert (lo.conversion.value, lo.date.value) != (hi.conversion.value, hi.date.value)


def test_the_series_panel_names_its_source_call(reference_page, reference_run):
    seq = reference_run.state.last("daily_series").seq
    assert "Daily values were read from tool call" in " ".join(parse(reference_page).text) and f'href="#call-{seq}"' in reference_page


def test_without_the_daily_tool_the_panel_says_it_shows_only_window_levels(reference_run):
    state = reference_run.state.model_copy(deep=True)
    call = state.last("daily_series")
    state.log[call.seq - 1] = call.model_copy(update={"result": {**call.result, "status": "insufficient_data"}})
    view = build_view(Investigation(state=state, result=reference_run.result))
    panel = next(s for s in view.steps if s.key == "locate").timeline
    page = render_html(view)
    assert panel.points == [] and wording.NO_DAILY_VALUES == panel.caveat
    assert "<polyline" not in page and "not a daily series" in " ".join(parse(page).text)
    assert shown_numbers(page) <= allowed_tokens(view, state)


def test_every_chart_mark_has_a_tooltip(reference_page):
    assert not re.findall(r"<(?:circle|rect|path)\b[^>]*(?<!/)>(?!<title>)", reference_page)
    assert not re.findall(r"<(?:circle|rect|path)\b[^>]*/>", reference_page)
    assert "the estimate being checked: " in reference_page and "contribution " in reference_page


def test_the_page_reflows_on_a_narrow_screen(reference_page):
    css = reference_page[reference_page.index("<style>") : reference_page.index("</style>")]
    assert "@media (max-width:640px)" in css and "grid-template-columns:1fr" in css and "overflow-x:auto" in css
    assert not re.search(r"\btd\b[^{}]*\{[^}]*overflow-wrap:anywhere", css) and 'name="viewport"' in reference_page


def test_the_page_says_which_data_it_is_about(reference_page, reference_run):
    visible = " ".join(parse(reference_page).text)
    catalog = reference_run.state.call(1).result["datasets"][0]
    assert catalog["metrics_sha256"] in visible and catalog["events_sha256"] in visible and "5,888" in visible
    assert catalog["date_range"][0] in visible and catalog["date_range"][1] in visible


def test_every_step_has_a_section_and_every_source_link_has_a_target(reference_page):
    ids = set(re.findall(r'id="([^"]+)"', reference_page))
    assert {f"step-{k}" for k in STEP_KEYS} <= ids
    hrefs = set(re.findall(r'href="#([^"]+)"', reference_page))
    assert hrefs <= ids and {f"call-{n}" for n in range(1, 14)} <= hrefs
    assert reference_page.count("<section") == 6 and reference_page.count("<h1>") == 1


def shown_numbers(page: str) -> set[str]:
    """Number tokens a reader sees, other than step numbers ("Step 4") and the call numbers in source superscripts."""
    visible = re.sub(r"\bStep \d\b", "Step", " ".join(parse(page).text))
    return tokens(visible)


def test_every_number_on_the_page_is_a_traced_value_and_call_numbers_appear_only_as_sources(reference_page, reference_view, reference_run):
    assert shown_numbers(reference_page) - allowed_tokens(reference_view, reference_run.state) == set()
    p = parse(reference_page)
    calls = {str(c.seq) for c in reference_run.state.log}
    assert p.sources and set(p.sources) <= calls and {str(n) for n in range(1, 14)} == set(p.sources)


def test_a_number_that_was_not_in_the_log_would_be_caught(reference_page, reference_view, reference_run):
    tampered = reference_page.replace("Step 5", "Step 5 and 0.123456 pp", 1)
    assert shown_numbers(tampered) - allowed_tokens(reference_view, reference_run.state) == {"0.123456", "123456"}


def test_uncertainty_is_never_dropped_every_interval_is_shown_with_its_level_and_bounds(reference_page, reference_view):
    visible = " ".join(parse(reference_page).text)
    intervals = [i for i in iter_traced(reference_view) if isinstance(i, TracedInterval)]
    assert len(intervals) >= 8
    for i in intervals:
        _, lo, hi, level = fmt_bounds(i)
        assert f"{lo} to {hi}" in visible and level in visible, i.label


def test_samples_sit_next_to_estimates(reference_page, reference_view):
    visible = " ".join(parse(reference_page).text)
    did = next(s for s in reference_view.steps if s.key == "estimate").estimates[0].estimate.effect_pp
    assert did.n and all(re.search(re.escape(n.label) + r"\s+" + re.escape(fmt_traced(n)), visible) for n in did.n)


def test_evidence_strength_alternatives_and_data_quality_are_on_the_page(reference_page, reference_view, reference_run):
    visible = " ".join(parse(reference_page).text)
    r = reference_run.result
    assert f"Evidence strength: {r.evidence.strength}" in visible and r.evidence.reason in visible
    assert all(t.value in visible for t in r.evidence.reasons)
    assert "Alternatives considered" in visible and all(a.value in visible for a in r.alternatives_considered)
    assert "Data-quality warning" in visible and "2026-09-20" in visible and "tracking break is a valid explanation" in visible
    assert "Flags raised" in visible and all(f.reason.value in visible for f in r.data_quality.flags)
    assert "Assumptions this estimate rests on" in visible and all(a.value in visible for a in r.causal_estimates[0].assumptions)
    assert "What remains unexplained" in visible and "not forced" in visible


def test_the_causal_claim_is_labelled_and_the_attribution_is_separate(reference_page, reference_run):
    visible = " ".join(parse(reference_page).text)
    assert "Causal claim: allowed" in visible and "separate, labelled claim" in visible
    claim = next(s.text for s in reference_run.result.statements if s.kind == "causal_claim")
    assert claim in visible


def test_static_wording_follows_the_scientist_rules(reference_page, reference_view, reference_run):
    visible = " ".join(parse(reference_page).text)
    # strip everything a tool or a validated reasoner wrote, then check what the console itself says
    sources = [str(t.value) for t in iter_traced(reference_view) if isinstance(t, Traced) and isinstance(t.value, str)]
    sources += [s.text for s in reference_run.result.statements]
    sources += [reference_run.result.evidence.reason, reference_run.result.causal_estimates[0].attribution_label, reference_run.result.causal_estimates[0].claim.reason]
    sources += [c.why for c in reference_run.result.candidates] + [reference_run.result.unexplained.note]
    sources += [n for s in reference_view.steps for n in s.stage_notes]
    for text in sorted(set(sources), key=len, reverse=True):
        visible = visible.replace(text, " ")
    assert not CAUSAL_WORDS.search(visible), CAUSAL_WORDS.search(visible).group(0)


def test_the_consoles_fixed_phrases_have_no_digits_or_cause_asserting_words():
    phrases = [v for k, v in vars(wording).items() if k.isupper() and isinstance(v, str)]
    phrases += list(view_module.STEP_TITLES.values()) + list(view_module.STEP_INTRO.values())
    assert phrases
    for text in phrases:
        assert not re.search(r"\d", text) and not CAUSAL_WORDS.search(text), text


def test_rendering_is_deterministic(reference_run):
    assert render_html(build_view(reference_run)) == render_html(build_view(reference_run))


def test_the_console_never_reads_the_answer_key():
    for path in (ROOT / "causelens" / "ui").glob("*.py"):
        assert "ground_truth" not in path.read_text(), path


# --------------------------------------------------------------------------- other investigations

def test_an_unconfirmed_change_shows_the_stop_and_the_steps_that_did_not_run(tmp_path):
    for seed in range(1, 6):
        m, e = scenario_data(seed, "none")
        inv = run(*write_frames(tmp_path, m, e))
        if inv.result.status == "stopped_change_not_confirmed":
            break
    else:
        pytest.fail("no seed stopped")
    view = build_view(inv)
    assert [s.status for s in view.steps] == ["stopped", "not_run", "not_run", "not_run", "not_run", "not_run"]
    page = render_html(view)
    visible = " ".join(parse(page).text)
    assert 'class="banner stop"' in page and inv.result.stopped_reason in visible and "Evidence strength: insufficient" in visible
    assert visible.count("This step did not run") == 5 and "No experiment designed" in visible and "No causal estimate" in visible
    assert shown_numbers(page) <= allowed_tokens(view, inv.state)
    assert parse(page).errors == []


def test_with_no_supported_candidate_the_page_says_so_and_still_recommends_an_experiment(tmp_path):
    metrics, events, _ = dataset_files(tmp_path, 43)
    inv = run(metrics, events)
    r = inv.result
    assert not any(c.verdict.value == "supported" for c in r.candidates) and r.evidence.strength == "insufficient"
    view = build_view(inv)
    visible = " ".join(parse(render_html(view)).text)
    assert "Evidence strength: insufficient" in visible and "No causal estimate." in visible and r.causal_estimate_note in visible
    assert "Alternatives considered" in visible
    assert all(a.value in visible for a in r.alternatives_considered) and "Causal claim" not in visible
    assert view.steps[5].experiment is not None and view.steps[5].experiment.purpose.value == "settle"
    assert shown_numbers(render_html(view)) <= allowed_tokens(view, inv.state)


def test_a_dataset_without_an_event_log_shows_the_skipped_steps(files):
    inv = run(files[0], None)
    view = build_view(inv)
    status = {s.key: s.status for s in view.steps}
    assert status["test"] == "skipped" and status["estimate"] == "skipped" and status["recommend"] == "skipped"
    visible = " ".join(parse(render_html(view)).text)
    assert "No candidate explanation was tested" in visible and "no event log" in visible
    assert "Evidence strength: insufficient" in visible


def test_text_from_the_data_is_escaped(tmp_path):
    metrics, events, _ = generate(42)
    hostile = '<img src=x onerror=alert(1)> & "quotes"'
    events = events.copy()
    events.loc[events.name == HERO, "name"] = hostile
    inv = run(*write_frames(tmp_path, metrics, events))
    page = render_html(build_view(inv))
    assert hostile not in page and "&lt;img src=x onerror=alert(1)&gt;" in page
    p = parse(page)
    assert "img" not in p.tags and not any(n.startswith("on") for _, n, _ in p.attrs) and p.errors == []
    assert hostile in " ".join(p.text)  # shown to the reader as text


def test_the_page_over_streamable_http_matches_in_process(files, reference_page):
    server = RunningServer(DatasetRegistry([DatasetConfig("reference", files[0], files[1])]))
    try:
        async def go():
            async with McpToolClient(server.url) as client:
                return await investigate(request(), client)

        over_http = asyncio.run(go())
    finally:
        server.stop()
    assert render_html(build_view(over_http)) == reference_page


# --------------------------------------------------------------------------- geometry

@given(st.floats(-1e6, 1e6, allow_nan=False), st.floats(-1e6, 1e6, allow_nan=False), st.floats(1, 2000), st.floats(0, 100))
def test_the_chart_scale_is_monotone_and_never_divides_by_zero(a, b, width, left):
    lo, hi = min(a, b), max(a, b)
    f = render_module._scale(lo, hi, width, left)
    assert f(lo) == pytest.approx(left) and f(hi) == pytest.approx(left + width if hi > lo else left)
    assert f(lo) <= f(hi)


def interval(est, lo, hi) -> TracedInterval:
    n = Traced(label="days", value=10, unit="days", call=1, path="/n")
    return TracedInterval(label="x", estimate=est, lower=lo, upper=hi, level=0.95, unit="pp", call=1, path="/x", n=[n])


def test_degenerate_charts_do_not_crash():
    assert "svg" in forest_svg([("zero", interval(0.0, 0.0, 0.0))], "z", "title")
    assert forest_svg([], "z", "title") == ""
    assert timeline_svg(TimelinePanel(first=None, last=None, pre=None, post=None, best_date=None, plausible=[], events=[], excluded=[], caveat="")) == ""


@given(st.lists(st.tuples(st.floats(-5, 5, allow_nan=False), st.floats(0, 5, allow_nan=False)), min_size=1, max_size=6))
def test_forest_charts_draw_any_set_of_intervals(rows):
    svg = forest_svg([(f"row{i}", interval(c, c - w, c + w)) for i, (c, w) in enumerate(rows)], "f", "t")
    assert svg.count("<circle") == len(rows) and "nan" not in svg and "inf" not in svg


# --------------------------------------------------------------------------- the command

def test_the_command_writes_the_page_and_reports_the_outcome(files, tmp_path, capsys):
    out = tmp_path / "console.html"
    assert cli.main(["--metrics", str(files[0]), "--events", str(files[1]), "--out", str(out)]) == 0
    assert "evidence strength: moderate" in capsys.readouterr().out
    page = out.read_text(encoding="utf-8")
    assert page.startswith("<!doctype html>") and "Step 6" in page and parse(page).errors == []


def test_the_command_reports_missing_data_and_bad_windows_as_usage_errors(tmp_path, capsys):
    assert cli.main(["--metrics", str(tmp_path / "absent.csv"), "--out", str(tmp_path / "x.html")]) == 2
    assert "python -m causelens.sim.generate" in capsys.readouterr().err
    assert cli.main(["--pre", "2026-8-1", "2026-10-13", "--out", str(tmp_path / "x.html")]) == 2
    assert "ISO date" in capsys.readouterr().err
    assert not (tmp_path / "x.html").exists()


def test_the_command_can_use_a_running_server(files, tmp_path, capsys):
    server = RunningServer(DatasetRegistry([DatasetConfig("reference", files[0], files[1])]))
    try:
        assert cli.main(["--server", server.url, "--out", str(tmp_path / "h.html")]) == 0
    finally:
        server.stop()
    assert "Evidence strength: moderate" in " ".join(parse((tmp_path / "h.html").read_text()).text)
    assert "status: completed" in capsys.readouterr().out


def test_the_command_runs_without_an_event_log(files, tmp_path, capsys):
    assert cli.main(["--metrics", str(files[0]), "--no-events", "--out", str(tmp_path / "n.html")]) == 0
    assert "evidence strength: insufficient" in capsys.readouterr().out

