"""Keep finding priorities simple enough for the team to explain.

The score is severity x likelihood x impact. Severity comes from the detector;
the team can change the likelihood and impact weights. Those two values are
assumptions, not measurements from our local tests or a user survey.
"""

MAX_RAW_SCORE = 125

ASSUMPTION_SOURCE = (
    "BudgetBench team estimate for this prototype. It is not measured and does not come from a survey. "
    "Replace these weights with evidence from your own app before relying on the ranking."
)

DEFAULT_ASSUMPTIONS = {
    "likelihood": {
        "normal": 5,          # A good connection is common enough to use as our baseline.
        "slow_network": 4,    # Our starting estimate for people using mobile data on budget phones.
        "intermittent": 3,    # An estimate for occasional connection drops.
        "packet_loss": 3,     # Kept near the intermittent profile until we have better data.
    },
    "impact_weight": {
        "load_data": 3,       # A user can often refresh and try again.
        "upload": 5,          # An upload may contain a form, photo or other user work.
    },
    "source": ASSUMPTION_SOURCE,
}


def priority_band(percent):
    if percent >= 50:
        return "High"
    if percent >= 25:
        return "Medium"
    return "Low"


def calculate_priority(severity, likelihood, impact_weight):
    """Return the score and its parts so the ranking is easy to check."""
    for name, value in (("severity", severity), ("likelihood", likelihood),
                        ("impact_weight", impact_weight)):
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 5:
            raise ValueError(f"{name} must be a whole number from 1 to 5, got {value!r}")

    raw = severity * likelihood * impact_weight
    percent = round(raw / MAX_RAW_SCORE * 100, 1)
    band = priority_band(percent)
    return {
        "severity": severity,
        "likelihood": likelihood,
        "impact_weight": impact_weight,
        "raw": raw,
        "percent": percent,
        "band": band,
        "formula": f"{severity} x {likelihood} x {impact_weight} = {raw} / {MAX_RAW_SCORE} = {percent}% ({band})",
    }


def score_finding(severity, profile, operation, assumptions):
    """Score a finding with the current team weights and explain each factor."""
    likelihood = int(assumptions["likelihood"][profile])
    impact = int(assumptions["impact_weight"][operation])
    result = calculate_priority(severity, likelihood, impact)
    result["explanation"] = (
        f"Severity {severity} (fixed for this detector type) x "
        f"Likelihood {likelihood} (assumed for profile '{profile}') x "
        f"Impact {impact} (assumed for operation '{operation}') = {result['raw']} out of "
        f"{MAX_RAW_SCORE} = {result['percent']}% -> {result['band']} priority. "
        f"Likelihood and impact are assumptions, not measurements."
    )
    return result
