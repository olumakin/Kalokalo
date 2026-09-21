from unittest.mock import MagicMock

import pytest

import src.webapp.auth as auth


class TestIsAdmin:
    def test_not_logged_in_is_never_admin(self, monkeypatch):
        import streamlit as st
        monkeypatch.setattr(st, "user", MagicMock(is_logged_in=False, email="anyone@example.com"))
        monkeypatch.setattr(st, "secrets", {"admin": {"emails": ["anyone@example.com"]}})
        assert auth.is_admin() is False

    def test_logged_in_but_not_on_allowlist_is_not_admin(self, monkeypatch):
        import streamlit as st
        monkeypatch.setattr(st, "user", MagicMock(is_logged_in=True, email="stranger@example.com"))
        monkeypatch.setattr(st, "secrets", {"admin": {"emails": ["admin@example.com"]}})
        assert auth.is_admin() is False

    def test_logged_in_and_on_allowlist_is_admin(self, monkeypatch):
        import streamlit as st
        monkeypatch.setattr(st, "user", MagicMock(is_logged_in=True, email="admin@example.com"))
        monkeypatch.setattr(st, "secrets", {"admin": {"emails": ["admin@example.com"]}})
        assert auth.is_admin() is True

    def test_email_match_is_case_insensitive(self, monkeypatch):
        import streamlit as st
        monkeypatch.setattr(st, "user", MagicMock(is_logged_in=True, email="Admin@Example.com"))
        monkeypatch.setattr(st, "secrets", {"admin": {"emails": ["admin@example.com"]}})
        assert auth.is_admin() is True

    def test_unconfigured_admin_secrets_degrades_to_not_admin(self, monkeypatch):
        import streamlit as st
        monkeypatch.setattr(st, "user", MagicMock(is_logged_in=True, email="admin@example.com"))
        monkeypatch.setattr(st, "secrets", MagicMock(__getitem__=MagicMock(side_effect=KeyError("admin"))))
        assert auth.is_admin() is False

    def test_broken_user_object_degrades_to_not_admin_not_raise(self, monkeypatch):
        import streamlit as st
        broken_user = MagicMock()
        type(broken_user).is_logged_in = property(lambda self: (_ for _ in ()).throw(RuntimeError("no context")))
        monkeypatch.setattr(st, "user", broken_user)
        assert auth.is_admin() is False


class TestRequireAdmin:
    def test_admin_passes_through_without_stopping(self, monkeypatch):
        monkeypatch.setattr(auth, "is_admin", lambda: True)
        # Must not raise/stop -- if it did, this test would fail with
        # streamlit's StopException propagating.
        auth.require_admin()

    def test_non_admin_warns_and_stops(self, monkeypatch):
        # Real behavior (verified separately via AppTest against the
        # actual admin pages): st.stop() halts the script entirely, so
        # nothing after require_admin() ever renders. In bare mode
        # (no real ScriptRunContext) st.stop() no-ops instead of
        # raising, so this test checks the call, not the halt itself.
        import streamlit as st
        monkeypatch.setattr(auth, "is_admin", lambda: False)
        warnings = []
        monkeypatch.setattr(st, "warning", lambda msg: warnings.append(msg))
        stop_calls = []
        monkeypatch.setattr(st, "stop", lambda: stop_calls.append(True))

        auth.require_admin()

        assert warnings == ["Restricted"]
        assert stop_calls == [True]
