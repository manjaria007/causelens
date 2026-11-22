"""The fixed phrases of the console. None contains a digit (so none can carry a number) or a cause-asserting word; a test enforces both."""
from __future__ import annotations

PAGE_TITLE = "CauseLens investigation"
NOT_AVAILABLE = "not available"
NOT_TESTED = "not tested"
DATA_TITLE = "Data"
FINGERPRINTS = "Fingerprints of the files behind every number"
NONE = "none"
NO_SAMPLE_SIZE = "the tool reports no sample size for this estimate"
NO_LOWER_BOUND = "no lower bound"
NO_UPPER_BOUND = "no upper bound"
NO_CHANGE = "no change"
STEP_NOT_RUN = "This step did not run: the investigation stopped earlier or could not reach it."
NO_CANDIDATES_TESTED = "No candidate explanation was tested."

TIMELINE_TITLE = "Level of conversion before and after, with the change point"
PLAUSIBLE_RANGE = "Dates the data cannot rule out as the onset"
EXCLUDED_DAY = "Excluded day"
TIMELINE_LEGEND = (
    "The shaded span is the plausible onset range, the dashed line the most plausible onset, red ticks are days excluded as possible "
    "tracking issues, and lettered triangles are the dates of logged events. Bands show each window's level with its interval."
)
WATERFALL_TITLE = "Where the change is concentrated"
WATERFALL_DESC = "Each bar is one segment's contribution to the overall change, stacked in rank order, for the partition"
DID_TITLE = "Difference-in-differences estimate"
PLACEBO_TITLE = "Placebo check"
ALTERNATIVES_TITLE = "Alternatives considered"
UNEXPLAINED_TITLE = "What remains unexplained"
STATEMENTS_TITLE = "Written summary (checked against the call log)"

PROVENANCE_TITLE = "Where the numbers come from"
PROVENANCE_NOTE = (
    "Every number links to the numbered tool call it was read from. Hovering a number shows its exact value and the position in that "
    "call's result. Arguments are listed by name; earlier results passed on are shown as the call they came from."
)
REASONER_NOTE = (
    "Written summaries come from the reasoner the run used; each sentence is a template whose numbers are references into the call log, "
    "and it is validated before it is shown. Charts are drawn from the same logged values."
)
