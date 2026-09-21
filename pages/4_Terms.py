"""Terms of Use — placeholder, pending legal review."""
from __future__ import annotations

import os

import streamlit as st

st.set_page_config(page_title="DVPE — Terms of Use", page_icon="📄", layout="wide")


def _resolve_rg_url() -> str:
    url = os.environ.get("RG_URL", "")
    if url:
        return url
    try:
        return st.secrets.get("RG_URL", "https://www.begambleaware.org/")
    except Exception:  # noqa: BLE001
        return "https://www.begambleaware.org/"


st.title("Terms of Use")
st.error("**DRAFT — pending legal review.** This page is placeholder text, not a reviewed legal document.")

st.markdown(
    f"""
### Not financial advice

Everything on this site — forecasts, probabilities, most-likely scorelines, and any
"shadow" staking figures shown to signed-in operators — is technical research output.
None of it is financial advice, a recommendation to place a bet, or a guarantee of any
outcome. Use of this site is entirely at your own risk.

### Age restriction

This site is not intended for anyone under 18 (or the legal gambling age in your
jurisdiction, if higher). By confirming your age on the home page, you represent that
you meet this requirement.

### Responsible gambling

If gambling is or may become a problem for you, resources are available:
[{_resolve_rg_url()}]({_resolve_rg_url()}).

### No warranty

The forecasts on this site are produced by an automated statistical model under active
technical validation. They may be wrong, incomplete, or unavailable at any time. This
site and its operators make no warranty of accuracy, completeness, or fitness for any
particular purpose.

### Data

Published predictions are stored and served from a managed database. Account
information for signed-in administrators is handled by a third-party identity
provider, not stored directly by this site.

---

*This placeholder will be replaced with reviewed legal terms before this deployment is
considered final.*
"""
)
