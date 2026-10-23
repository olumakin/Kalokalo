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
    each value has "baseline", "flagged", and "diff" sub-dicts with
    n/mean_clv (or mean_diff for "diff")/ci_lo/ci_hi/insufficient_sample.

    Decision rule, applied to leagues with a sufficient flagged-bet
    sample for *both* the flagged-CLV and diff-vs-baseline statistics
    (insufficient_sample leagues can't support any decision and are
    excluded rather than silently counted as failing):
      - lower bound above 0 on *both* flagged CLV and the flagged-vs-
        baseline diff, in some league(s) -> proceed with those leagues
        only. Flagged CLV alone isn't enough: a league can clear zero
        on flagged CLV purely because the draw price drifts favorably
        across the board (baseline positive too), with the model's
        selection adding nothing over naively betting every draw --
        the diff criterion is what rules that out. Added as a
        permanent gate criterion after a WP3 review found exactly this
        pattern in three of four leagues the gate had cleared on
        flagged CLV alone.
      - otherwise, if the upper CLV bound is at or below 0 in every
        evaluable league -> reposition as a forecasting tool (no league
        shows even a plausible edge).
      - otherwise -> inconclusive (some interval straddles 0 without
        clearing it — more data needed, not a verdict either way).
    """
    evaluable = {
        lg: d for lg, d in clv_by_league.items()
        if not d["flagged"]["insufficient_sample"] and not d["diff"]["insufficient_sample"]
    }
    if not evaluable:
        return {
            "decision": GATE_INCONCLUSIVE,
            "reason": "no league has a sufficient flagged-bet sample to decide from",
            "leagues": [],
        }

    proceed_leagues = sorted(
        lg for lg, d in evaluable.items() if d["flagged"]["ci_lo"] > 0 and d["diff"]["ci_lo"] > 0
    )
    if proceed_leagues:
        return {
            "decision": GATE_PROCEED_SUBSET,
            "reason": (
                f"lower bound above 0 on both flagged CLV and the vs.-baseline diff in: "
                f"{', '.join(proceed_leagues)}"
            ),
            "leagues": proceed_leagues,
        }

    if all(d["flagged"]["ci_hi"] <= 0 for d in evaluable.values()):
        return {
            "decision": GATE_REPOSITION,
            "reason": "upper CLV bound at or below 0 in every evaluable league",
            "leagues": [],
        }

    return {
        "decision": GATE_INCONCLUSIVE,
        "reason": "CLV interval straddles 0, or doesn't beat the naive baseline, in every evaluable league",
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
    lines.append(
        "| League | Baseline n | Baseline CLV | Flagged n | Flagged CLV | Flagged 95% CI | "
        "Diff vs. baseline | Diff 95% CI |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for league in sorted(clv_by_league):
        b, f, d = clv_by_league[league]["baseline"], clv_by_league[league]["flagged"], clv_by_league[league]["diff"]
        flagged_ci = (
            "n/a" if f["insufficient_sample"] else f"[{_fmt_pct(f['ci_lo'])}, {_fmt_pct(f['ci_hi'])}]"
        )
        diff_ci = (
            "n/a" if d["insufficient_sample"] else f"[{_fmt_pct(d['ci_lo'])}, {_fmt_pct(d['ci_hi'])}]"
        )
        diff_val = "n/a (insufficient sample)" if d["insufficient_sample"] else _fmt_pct(d["mean_diff"])
        lines.append(
            f"| {league} | {b['n']} | {_fmt_pct(b['mean_clv'])} | {f['n']} | {_fmt_pct(f['mean_clv'])} | "
            f"{flagged_ci} | {diff_val} | {diff_ci} |"
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
