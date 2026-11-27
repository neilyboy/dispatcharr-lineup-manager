import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lineup_manager.stations import parse_station_name


class StationParserTests(unittest.TestCase):
    def test_state_market_pipe_format(self):
        row = parse_station_name("IL | Springfield | ABC 20 WICS")
        self.assertEqual(row["state"], "IL")
        self.assertEqual(row["city"], "Springfield")
        self.assertEqual(row["call"], "WICS")
        self.assertEqual(row["network"], "ABC")
        self.assertEqual(row["channel"], "20")
        self.assertEqual(row["station_key"], "call:wics")

    def test_call_city_format_uses_same_station_key(self):
        row = parse_station_name("US ABC 20 (WICS) Springfield")
        self.assertEqual(row["call"], "WICS")
        self.assertEqual(row["channel"], "20")
        self.assertEqual(row["station_key"], "call:wics")

    def test_bracket_provider_format(self):
        row = parse_station_name("[US| Example Provider] [Springfield_IL] ABC-WICS")
        self.assertEqual(row["state"], "IL")
        self.assertEqual(row["city"], "Springfield")
        self.assertEqual(row["call"], "WICS")

    def test_subchannel_suffix_is_part_of_identity(self):
        main = parse_station_name("TN-VA | Tri-Cities | CBS WJHL")
        sub = parse_station_name("TN-VA | Tri-Cities | ABC WJHL-D2")
        self.assertNotEqual(main["station_key"], sub["station_key"])

    def test_inline_market_format(self):
        row = parse_station_name("TX | Beaumont CBS 6 KFDM")
        self.assertEqual(row["state"], "TX")
        self.assertEqual(row["city"], "Beaumont")
        self.assertEqual(row["call"], "KFDM")
        self.assertEqual(row["channel"], "6")


class NationalTests(unittest.TestCase):
    def test_english_networks_only(self):
        from lineup_manager.stations import ENGLISH_NETWORKS, network_for
        self.assertIn(network_for("TX | Austin | CBS KEYE"), ENGLISH_NETWORKS)
        self.assertNotIn(network_for("Austin | Univision 62 KAKW"), ENGLISH_NETWORKS)

    def test_display_name_matches_live_tv_parser(self):
        from lineup_manager.stations import display_name
        self.assertEqual(display_name({"city": "Peoria", "network": "CBS", "call": "WMBD", "channel": "31"}), "Peoria: CBS 31 WMBD")


if __name__ == "__main__":
    unittest.main()
