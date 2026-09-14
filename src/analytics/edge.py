"""
Expected value, qualification, and Fractional Kelly position sizing.
See PID section 4.2.
"""
from __future__ import annotations


def calculate_ev(model_p_draw: float, market_odds_draw: float) -> float:
    """EV = (P_model(Draw) * Odds_market_draw) - 1"""
    return model_p_draw * market_odds_draw - 1.0


def qualifies(model_p_draw: float, market_p_draw: float, ev: float, min_ev: float = 0.03) -> bool:
    """A bet qualifies iff EV >= +3.0% AND the model draw probability
    exceeds the de-vigged market draw probability."""
    return ev >= min_ev and model_p_draw > market_p_draw


def kelly_fraction(p: float, odds: float, c: float = 0.15) -> float:
    """Fractional Kelly stake as a fraction of bankroll.

    f* = c * (b*p - q) / b,  b = odds - 1, q = 1 - p.
    Clamped at 0 (never recommend a negative/short stake).
    """
    b = odds - 1.0
    if b <= 0:
        return 0.0
    q = 1.0 - p
    f = c * ((b * p - q) / b)
    return max(f, 0.0)


def apply_risk_caps(
    stakes: dict[str, float],
    single_match_cap: float = 0.025,
    daily_slate_cap: float = 0.08,
) -> dict[str, float]:
    """Enforce the dual-layer exposure cap (PID 4.2):
      1. Clip each stake to `single_match_cap` of bankroll.
      2. If the resulting total exceeds `daily_slate_cap`, scale every
         stake down proportionally so the slate total equals the cap.
    """
    capped = {match_id: min(f, single_match_cap) for match_id, f in stakes.items()}
    total = sum(capped.values())
    if total > daily_slate_cap and total > 0:
        scale = daily_slate_cap / total
        capped = {match_id: f * scale for match_id, f in capped.items()}
    return capped
