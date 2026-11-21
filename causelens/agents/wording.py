"""The fixed phrases the orchestrator writes itself. None contains a digit, so none can carry a number. A test enforces it."""
from __future__ import annotations

DATA_QUALITY_NOTE = (
    "A tracking break is a valid explanation for a move in conversion. Flags mark possible data issues, not confirmed ones; "
    "flagged days are excluded from the estimates below and the exclusion is stated."
)

MULTIPLE_COMPARISONS = (
    "Many segments were scanned. The false discovery rate was controlled (Benjamini-Hochberg) and the adjusted significance is "
    "what the localization uses."
)

ATTRIBUTION_LABEL = (
    "The estimate is a differential effect of the focal segment against the comparison. Attributing it to the named release is "
    "a separate, labelled claim that holds only if the comparison was unaffected by the release and the candidate's lag "
    "assumption is right."
)

UNEXPLAINED_NOTE = (
    "Shares are shown as the tools returned them and are not forced to add up to a whole. A negative remaining share means the "
    "localized segment moved more than the overall change and other segments moved the other way."
)
UNEXPLAINED_NO_EVENT = (
    "No logged event is supported, so none of the change is attributed to an event; the whole change is unexplained by the "
    "event log."
)

WHY_LEADING = "Kept as the leading explanation: supported under its stated lag, with the checks reported next to the estimate."
WHY_ALTERNATIVE = (
    "Kept as an alternative: the evidence neither supports nor rejects it. The reasons and the lag sensitivity say what would "
    "change that."
)
WHY_REJECTED_SOLE_SOURCE = (
    "Rejected on positive statistical evidence: the timing contrast rejects it as the sole source of the localized change. "
    "This does not show that the event contributed nothing."
)
WHY_REJECTED_PATTERN = (
    "Rejected on positive statistical evidence: a direct test contradicts the effect pattern declared for it. This does not "
    "show that the event contributed nothing."
)
WHY_REJECTED_EXPOSURE = (
    "Rejected on positive evidence: the localized change is disjoint from the visitors the event could have touched. This does "
    "not show that the event contributed nothing to other visitors."
)
WHY_REJECTED_UNSPECIFIED = "Rejected by the tool; the reasons give the ground."
SHARE_SET_NOTE = (
    "The set describes calendar steps under the lag model: the share of the localized change that begins at this onset date. It "
    "is not a release's causal share."
)

NO_ESTIMATE_NO_SUPPORTED = "No candidate was supported, so no causal estimate was produced."
NO_ESTIMATE_NOT_RUN = "The investigation stopped before candidate testing, so no causal estimate was produced."
NO_ESTIMATE_NO_EVENT_LOG = "The dataset has no event log, so no candidate was tested and no causal estimate was produced."
NO_ESTIMATE_INCOMPLETE = "The stage that produces the estimate did not complete, so no causal estimate was produced."

STRENGTH_NO_SUPPORTED = (
    "No candidate explanation is supported by the tests, so the evidence cannot support a causal claim. See the candidate "
    "verdicts for the reasons."
)
STRENGTH_STOPPED_NOT_CONFIRMED = (
    "The change is not distinguishable from noise, so there is nothing to attribute. The investigation stopped (the "
    "additional data that would be needed is listed with the confirmation)."
)
STRENGTH_STOPPED_DATA = "There is too little usable data to confirm the change, so the investigation stopped."
STRENGTH_NOT_LOCALIZED = "The change could not be localized to a segment, so no candidate could be tested against a focal segment."
STRENGTH_NO_EVENT_LOG = "The dataset has no event log, so no candidate explanation could be tested."
STRENGTH_TOOL_ERROR = "A tool call failed, so the investigation is incomplete."
STRENGTH_WEAKEST_SUPPORTED = (
    "Strength is that of the weakest supported candidate's evidence assessment, reported with the tool's own reasons."
)

STRENGTH_DESIGN_FAILED = "The experiment design call failed after testing completed; the strength is that of the completed tests."
NOT_RUN_FAILED = "The experiment design call failed, so no recommendation was produced."
NOT_RUN_NO_EVENT_LOG = "The dataset has no event log, so no experiment was designed."
NOT_RUN_SKIPPED = "No experiment was designed: the tests produced no focal segment to design for."
NOT_RUN_STOPPED = "The investigation stopped before there was anything to test, so no experiment is recommended."
NOT_RUN_NO_CANDIDATE_TESTING = "Candidate testing did not run, so no experiment design was produced."
