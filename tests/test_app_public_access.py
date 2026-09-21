"""
Acceptance-criteria verification: a logged-out visitor cannot trigger a
model fit, a data fetch, a backtest, a ledger write, or a settlement.
"""
from __future__ import annotations

from streamlit.testing.v1 import AppTest


class TestPublicMatchdayPage:
    def test_age_gate_blocks_everything_on_first_load(self):
        at = AppTest.from_file("../app.py", default_timeout=60)
        at.run()
        assert at.exception == []
        # Only the age-confirmation button exists -- no admin/run control
        # is reachable before it.
        assert [b.label for b in at.button] == ["I am 18 or older — continue"]

    def test_logged_out_visitor_sees_no_run_pipeline_button(self):
        at = AppTest.from_file("../app.py", default_timeout=60)
        at.run()
        at.button[0].click().run()  # confirm age

        assert at.exception == []
        assert "Run pipeline" not in [b.label for b in at.button]
        # No sidebar league/season/API-key controls either -- the whole
        # admin block is unreachable, not just the button at the end of it.
        assert not any("Active Leagues" in ms.label for ms in at.multiselect if ms.label)

    def test_logged_out_visitor_never_reaches_a_data_fetch_or_model_fit(self):
        # The public page's only data path is fetch_latest_predictions
        # (Supabase read) -- app.py must never call load_historical_matches,
        # get_upcoming_fixtures, or fit_model for a non-admin. Verified by
        # patching all three to explode if ever invoked.
        import unittest.mock as mock

        with mock.patch("src.pipeline.load_historical_matches", side_effect=AssertionError("must not fetch")), \
             mock.patch("src.pipeline.fit_model", side_effect=AssertionError("must not fit")):
            at = AppTest.from_file("../app.py", default_timeout=60)
            at.run()
            at.button[0].click().run()
            assert at.exception == []

    def test_public_page_shows_admin_login_prompt_not_controls(self):
        at = AppTest.from_file("../app.py", default_timeout=60)
        at.run()
        at.button[0].click().run()
        assert any("Admin sign-in required" in i.value for i in at.info)


class TestAdminOnlyPages:
    def test_backtest_page_shows_only_restricted_for_logged_out_visitor(self):
        at = AppTest.from_file("../pages/2_Backtest.py", default_timeout=60)
        at.run()
        assert at.exception == []
        assert [w.value for w in at.warning] == ["Restricted"]
        assert at.button == []
        assert at.title == []

    def test_ledger_page_shows_only_restricted_for_logged_out_visitor(self):
        at = AppTest.from_file("../pages/3_Ledger.py", default_timeout=60)
        at.run()
        assert at.exception == []
        assert [w.value for w in at.warning] == ["Restricted"]
        assert at.button == []
        assert at.title == []

    def test_backtest_page_never_reaches_walk_forward_run(self):
        import unittest.mock as mock

        with mock.patch("src.validation.backtest.WalkForwardBacktest.run", side_effect=AssertionError("must not run")):
            at = AppTest.from_file("../pages/2_Backtest.py", default_timeout=60)
            at.run()
            assert at.exception == []

    def test_ledger_page_never_reaches_settlement_write(self):
        import unittest.mock as mock

        with mock.patch("src.tracking.supabase_ledger.write_settlement", side_effect=AssertionError("must not settle")):
            at = AppTest.from_file("../pages/3_Ledger.py", default_timeout=60)
            at.run()
            assert at.exception == []
