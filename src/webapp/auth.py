"""
Admin authentication for the public DVPE app (Phase 0 revised).

Uses Streamlit's built-in OIDC support (st.login/st.user/st.logout —
requires an [auth] block in .streamlit/secrets.toml pointing at a real
OIDC provider; see README's deployment section) plus an admin email
allow-list in st.secrets. Deliberately the only module under src/ that
imports streamlit directly outside app.py/pages/*.py — auth is a
web-app concern, not a pipeline one, but it's small enough and
Streamlit-specific enough that it doesn't belong in src/ingestion or
src/models either.
"""
from __future__ import annotations

import streamlit as st


def _admin_emails() -> set[str]:
    try:
        emails = st.secrets["admin"]["emails"]
    except Exception:  # noqa: BLE001 — [admin] not configured yet is not a crash
        return set()
    return {e.lower() for e in emails}


def is_admin() -> bool:
    """True only for a signed-in user whose email is on the allow-list.

    Wrapped defensively: an unconfigured [auth]/[admin] secrets block
    (e.g. before the OIDC provider is set up) must degrade to "not
    admin" so the public page keeps rendering, never to a crash — same
    pattern as this app's other optional-secrets lookups
    (_resolve_odds_api_key, _resolve_rg_url in app.py).
    """
    try:
        user = st.user
        if not user.is_logged_in:
            return False
        email = (user.email or "").lower()
    except Exception:  # noqa: BLE001
        return False
    return email in _admin_emails()


def require_admin() -> None:
    """Call at the top of an admin-only page. Renders exactly
    "Restricted" and nothing else, then halts the script for anyone
    who isn't an authenticated admin — no page content, no data query,
    no control, executes below this call for them."""
    if not is_admin():
        st.warning("Restricted")
        st.stop()
