"""
scoring.py - turns a finding into a priority score you can explain to anyone.

    Priority (raw) = Severity x Likelihood x Impact weight     (each 1-5, so max 125)
    Priority (%)   = raw / 125 x 100

Where the three numbers come from:
  Severity      - fixed per detector type (see detectors.py). How bad is it when it happens?
  Likelihood    - how often we ASSUME users are in that condition (per profile).
  Impact weight - how important we ASSUME that operation is to the user (per operation).

IMPORTANT: Likelihood and impact weight are TEAM ASSUMPTIONS, not measurements.
Our local tests cannot tell us how many Indian phone users are on slow networks.
That is why they are editable in the dashboard and saved in data/assumptions.json.
"""

MAX_RAW_SCORE = 125

ASSUMPTION_SOURCE = (
    "PhoneReality team assumption for this prototype - NOT measured and NOT taken "
    "from any survey. Replace with real data (for example your app's own analytics) "
    "before relying on the ranking."
)

DEFAULT_ASSUMPTIONS = {
    "likelihood": {
        "normal": 5,          # everyone is sometimes on a good connection
        "slow_network": 4,    # assumed common for budget phones on mobile data
        "intermittent": 3,    # assumed: drops happen, but less often than slowness
        "packet_loss": 3,     # assumed: same as intermittent
    },
    "impact_weight": {
        "load_data": 3,       # user can usually pull-to-refresh
        "upload": 5,          # user's own data / action (form, photo, payment-like)
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
    """Return a dict with the raw score, the percentage, the band and a written explanation."""
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
    """Score one finding using the (possibly user-edited) assumptions and explain each factor."""
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
