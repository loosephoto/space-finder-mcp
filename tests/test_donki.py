"""donki の既定期間（_default_window）の回帰テスト。

DONKI の startDate/endDate は UTC の日付境界。既定窓をホストのローカル日付
（date.today()）で組むと UTC 以外の環境で窓が 1 日ずれる（東京では未明 9 時間）。
"""
import datetime
import unittest
from unittest import mock


class _FixedUTC:
    """donki.datetime の代用品。now() は常に 2026-11-07 20:00 UTC（= JST 11/8 05:00）。"""

    timezone = datetime.timezone
    timedelta = datetime.timedelta

    class datetime:
        @staticmethod
        def now(tz=None):
            fixed = datetime.datetime(2026, 11, 7, 20, 0, tzinfo=datetime.timezone.utc)
            return fixed.astimezone(tz) if tz else fixed


class DefaultWindowTests(unittest.TestCase):
    def test_default_window_uses_utc_date(self):
        """endDate は UTC の日付（ローカル JST なら 1 日進んだ日付にならない）。"""
        from space_finder_mcp import donki
        with mock.patch.object(donki, "datetime", _FixedUTC):
            w = donki._default_window(7)
        self.assertEqual(w["endDate"], "2026-11-07")   # UTC 基準。date.today() なら JST で 2026-11-08
        self.assertEqual(w["startDate"], "2026-10-31")


if __name__ == "__main__":
    unittest.main()
