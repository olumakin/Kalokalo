"""
WP3 gate decision: turns the per-league CLV evaluation
(src.validation.clv.evaluate_league_clv) into a programmatic go/no-go
decision, plus a human-readable one-page summary.

The decision is computed on numeric bounds, never on a pre-formatted
string — formatting only happens in render_gate_report_markdown, so
the decision itself stays testable and machine-actionable.
"""
from __future__ import annotations

import pandas as pd

GATE_REPOSITION = "reposition_as_forecasting_tool"
GATE_PROCEED_SUBSET = "proceed_with_leagues"
GATE_INCONCLUSIVE = "inconclusive"


def compute_gate_decision(clv_by_league: dict[str, dict]) -> dict:
    """clv_by_league: {league: evaluate_league_clv(...) result}, i.e.
    each value has "baseline" and "flagged" sub-dicts with n/mean_clv/
    ci_lo/ci_hi/insufficient_sample.

    Decision rule, applied to leagues with a sufficient flagged-bet
    sample only (insufficient_sample leagues can't support any
    decision and are excluded rather than silently counted as failing):
      - lower CLV bound above 0 in some league(s) -> proceed with those
        leagues only (their flagged bets show a real edge over the
        market's own closing move, not just the baseline).
      - otherwise, if the upper CLV bound is at or below 0 in every
        evaluable league -> reposition as a forecasting tool (no league
        shows even a plausible edge).
      - otherwise -> inconclusive (some interval straddles 0 without
        clearing it — more data needed, not a verdict either way).
    """
    evaluable = {
        lg: d for lg, d in clv_by_league.items()
        if not d["flagged"]["insufficient_sample"]
    }
    if not evaluable:
        return {
            "decision": GATE_INCONCLUSIVE,
            "reason": "no league has a sufficient flagged-bet sample to decide from",
            "leagues": [],
        }

    proceed_leagues = []
    for lg, d in evaluable.items():
        f = d["flagged"]
        b = d["baseline"]
        # Gate rule (A06): must have strictly positive lower bound AND exceed baseline CLV
        if f["ci_lo"] > 0 and f["mean_clv"] > b["mean_clv"]:
            proceed_leagues.append(lg)

    proceed_leagues = sorted(proceed_leagues)
    if proceed_leagues:
        return {
            "decision": GATE_PROCEED_SUBSET,
            "reason": f"lower CLV bound > 0 and beats baseline in: {', '.join(proceed_leagues)}",
            "leagues": proceed_leagues,
        }

    all_underperforming = all(
        d["flagged"]["ci_hi"] <= 0 or d["flagged"]["mean_clv"] <= d["baseline"]["mean_clv"]
        for d in evaluable.values()
    )
    if all_underperforming:
        return {
            "decision": GATE_REPOSITION,
            "reason": "no evaluable league demonstrates statistically positive edge above baseline",
            "leagues": [],
        }

    return {
        "decision": GATE_INCONCLUSIVE,
        "reason": "CLV interval straddles 0 in every evaluable league, none clears it",
        "leagues": [],
    }


def summarize_exclusions(exclusions: pd.DataFrame) -> dict[str, int]:
    """Count of excluded fixtures by reason code, for the report."""
    if exclusions.empty:
        return {}
    return exclusions["skip_reason"].value_counts().to_dict()


def _fmt_pct(x: float) -> str:
    return "n/a (insufficient sample)" if pd.isna(x) else f"{x:+.2%}"


def render_gate_report_markdown(
    clv_by_league: dict[str, dict],
    decision: dict,
    exclusions: pd.DataFrame | None = None,
    three_way_by_league: dict[str, dict] | None = None,
) -> str:
    """One-page plain-text/markdown summary. Numbers are formatted only
    here — compute_gate_decision and evaluate_league_clv never produce
    pre-formatted strings, so the decision stays testable independent
    of presentation."""
    lines = ["# WP3 Gate Report", ""]
    lines.append(f"**Decision: {decision['decision']}**")
    lines.append(f"Reason: {decision['reason']}")
    if decision["leagues"]:
        lines.append(f"Leagues cleared: {', '.join(decision['leagues'])}")
    lines.append("")

    lines.append("## CLV by league")
    lines.append("")
    lines.append("| League | Baseline n | Baseline CLV | Flagged n | Flagged CLV | Flagged 95% CI |")
    lines.append("|---|---|---|---|---|---|")
    for league in sorted(clv_by_league):
        b, f = clv_by_league[league]["baseline"], clv_by_league[league]["flagged"]
        flagged_ci = (
            "n/a" if f["insufficient_sample"] else f"[{_fmt_pct(f['ci_lo'])}, {_fmt_pct(f['ci_hi'])}]"
        )
        lines.append(
            f"| {league} | {b['n']} | {_fmt_pct(b['mean_clv'])} | {f['n']} | {_fmt_pct(f['mean_clv'])} | {flagged_ci} |"
        )
    lines.append("")

    if three_way_by_league:
        lines.append("## Three-way scoring by league")
        lines.append("")
        lines.append("| League | Log-loss | RPS |")
        lines.append("|---|---|---|")
        for league in sorted(three_way_by_league):
            m = three_way_by_league[league]
            lines.append(f"| {league} | {m.get('log_loss', float('nan')):.4f} | {m.get('rps', float('nan')):.4f} |")
        lines.append("")

    if exclusions is not None and not exclusions.empty:
        lines.append("## Exclusions by reason")
        lines.append("")
        for reason, count in summarize_exclusions(exclusions).items():
            lines.append(f"- {reason}: {count}")
        lines.append("")

    return "\n".join(lines)
