"""The three roles: a prompt plus an allowed subset of MCP tools. Not separate services (design, "Agents").

The prompt is what a model-backed ``Reasoner`` receives as its system message. It carries ``scientist-rules.md`` verbatim, so
the rules the report must follow are the file's text, not a paraphrase. The orchestrator enforces the tool subset, and
``causelens.agents.trace`` enforces the wording and numeric rules on whatever a ``Reasoner`` writes.

The design table lists the Detective's tools as four and the Scientist's as five. Two entries are added because the
analytics need them and Task 7 exposes them: ``list_datasets`` (dataset fingerprints for the report) and
``match_event_candidates`` (Task 3.3) for the Detective, and ``test_candidates`` (the Task 5 entry point, which runs
``test_event_scope`` for every candidate with the joint timing model) for the Scientist.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from causelens.agents.state import Stage

STEERING_DIR = Path(__file__).resolve().parents[2] / ".kiro" / "steering"
RULES_FILE = "scientist-rules.md"


class SteeringNotFound(FileNotFoundError):
    """The steering file the prompts are built from is missing."""


def scientist_rules() -> str:
    path = STEERING_DIR / RULES_FILE
    if not path.is_file():
        raise SteeringNotFound(f"{path} not found; the role prompts embed it verbatim")
    return path.read_text(encoding="utf-8").strip()


OUTPUT_CONTRACT = """\
How you write (enforced by code, not by trust):
- You never compute, round or invent a number. Every number or date in your text is a placeholder {name} that you bind to a
  reference (call, path, format) into the logged result of an MCP tool call. Your own sentences contain no digits.
- Use "coincides with", "is concentrated in", "is consistent with". Words that assert a cause are rejected unless the
  statement is a causal_claim that cites a difference-in-differences result whose checks passed, together with that
  result's assumptions.
- Always give the strength of evidence (strong, moderate, weak, insufficient) and the reason.
- Consider at least one alternative to the leading explanation and say why it was kept or rejected.
- Say what part of the change remains unexplained. Do not force attributions to add up to 100%.
- Never fill a gap with a plausible-sounding story; if the evidence is insufficient, say so and recommend the experiment."""


@dataclass(frozen=True)
class Role:
    name: str
    stages: tuple[Stage, ...]
    tools: frozenset[str]
    instructions: str

    def system_prompt(self) -> str:
        return "\n\n".join([
            f"You are the {self.name} in a CauseLens metric investigation.",
            self.instructions,
            f"Tools you may call: {', '.join(sorted(self.tools))}.",
            OUTPUT_CONTRACT,
            f"Scientist rules ({RULES_FILE}), verbatim:\n\n{scientist_rules()}",
        ])


DETECTIVE = Role(
    name="Detective",
    stages=("validate", "confirm", "change_point", "localize"),
    tools=frozenset({
        "list_datasets", "check_data_quality", "confirm_change", "detect_change_point",
        "match_event_candidates", "decompose_change",
    }),
    instructions=(
        "Check the data first (a tracking break is a valid explanation), confirm the change is real, find when it started and "
        "where it is concentrated. Describe where and when. Do not say why."
    ),
)

SCIENTIST = Role(
    name="Scientist",
    stages=("test", "estimate"),
    tools=frozenset({
        "test_candidates", "test_event_scope", "estimate_did", "run_placebo", "check_pre_trends", "project_impact",
    }),
    instructions=(
        "Test every candidate explanation with the deterministic tools and report each verdict with its stated lag. "
        "Estimate the differential effect of a supported candidate only with its checks, and state the assumptions beside the "
        "number. Rejecting an event as the sole source does not mean it contributed nothing."
    ),
)

EXPERIMENT_DESIGNER = Role(
    name="Experiment designer",
    stages=("recommend",),
    tools=frozenset({"power_analysis", "design_experiment"}),
    instructions=(
        "Recommend the randomized experiment that would settle the question, or confirm a strong result. Sample size and "
        "duration come from the power tools, never from you."
    ),
)

ROLES: dict[str, Role] = {r.name: r for r in (DETECTIVE, SCIENTIST, EXPERIMENT_DESIGNER)}
