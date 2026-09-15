import sys
import unittest
from types import SimpleNamespace

import requests

sys.path.insert(0, "src")


class RegressionTests(unittest.TestCase):
    def test_neo_error_content_redacts_api_key(self):
        from space_finder_mcp import nasa

        class Response:
            status_code = 500
            headers = {}

        def fail(*args, **kwargs):
            error = requests.HTTPError(
                "500 Server Error: https://api.nasa.gov/neo/rest/v1/feed?api_key=SECRET"
            )
            error.response = Response()
            raise error

        old_get = requests.get
        requests.get = fail
        nasa.neo_today.cache_clear()
        try:
            result = nasa.neo_today("SECRET")
        finally:
            requests.get = old_get

        self.assertNotIn("SECRET", result.content[0].text)
        self.assertIn("api_key=***", result.content[0].text)

    def test_iss_invalid_timestamp_returns_error_result(self):
        from space_finder_mcp import iss

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "iss_position": {"latitude": "1", "longitude": "2"},
                    "timestamp": "not-a-timestamp",
                }

        old_get = requests.get
        requests.get = lambda *args, **kwargs: Response()
        try:
            result = iss.iss_now()
        finally:
            requests.get = old_get

        self.assertEqual(result.structuredContent["error"], "bad timestamp")

    def test_solar_system_reports_planet_calculation_failure(self):
        from space_finder_mcp import solar_system

        class Angle:
            degrees = 10.0

        class Vector:
            def __sub__(self, other):
                return self

            def distance(self):
                return SimpleNamespace(au=1.0)

            def frame_latlon(self, frame):
                return Angle(), Angle(), Angle()

        class Body:
            def __init__(self, broken=False):
                self.broken = broken

            def at(self, time):
                if self.broken:
                    raise RuntimeError("planet unavailable")
                return Vector()

        class FakeTime:
            tt = 2460000.5

            def utc_strftime(self, fmt):
                return "2023-02-25 00:00 UTC"

        class FakeLoader:
            def timescale(self):
                return SimpleNamespace(utc=lambda *args: FakeTime())

        eph = {"sun": Body()}
        for _, key, _, _ in solar_system._PLANETS:
            eph[key] = Body(broken=(key == solar_system._PLANETS[0][1]))

        old_load = solar_system._load
        solar_system._load = lambda: (FakeLoader(), eph)
        try:
            result = solar_system._compute("2023-02-25T00:00:00Z")
        finally:
            solar_system._load = old_load

        self.assertEqual(result["planet_errors"], [
            {"name": solar_system._PLANETS[0][0], "error": "planet unavailable"}
        ])
