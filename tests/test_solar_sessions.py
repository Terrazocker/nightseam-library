"""Solar sessions: physical boundaries, shared-engine parity and USNO anchors."""
import json
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app import solar

BERLIN = (52.52, 13.405)


def metadata(stamp, site=BERLIN):
    return solar.session_metadata(stamp, *site)


def test_whole_winter_night_not_adjacent_capture_gap():
    evening = metadata("2026-12-21T16:00:00")
    morning = metadata("2026-12-22T06:00:00")
    assert evening == morning
    assert evening["session_kind"] == "night"
    assert (datetime.fromisoformat(evening["session_end"])
            - datetime.fromisoformat(evening["session_start"])) > timedelta(hours=16)
    following_night = metadata("2026-12-22T16:00:00")
    assert evening["session_key"] != following_night["session_key"]


@pytest.mark.parametrize("boundary,expected", [("session_start", "day"), ("session_end", "night")])
def test_exact_half_open_sunrise_and_sunset(boundary, expected):
    daytime = metadata("2026-08-22T12:00:00")
    transition = datetime.fromisoformat(daytime[boundary])
    before = metadata((transition - timedelta(microseconds=1)).isoformat())
    at = metadata(transition.isoformat())
    after = metadata((transition + timedelta(microseconds=1)).isoformat())
    assert at["session_kind"] == expected
    assert after == at
    assert before["session_key"] != at["session_key"]


def test_midnight_offsets_and_upload_order_do_not_change_identity():
    first = metadata("2026-08-22T23:00:00")
    after_midnight = metadata("2026-08-23T01:00:00")
    equivalent_offset = metadata("2026-08-23T03:00:00+02:00")
    assert first == after_midnight == equivalent_offset
    assert metadata("2026-08-22T21:00:00") == first
    assert metadata("2026-08-23T12:00:00")["session_kind"] == "day"


def test_adjacent_days_cannot_chain_into_one_session():
    keys = [metadata(f"2026-08-{day:02}T12:00:00")["session_key"] for day in range(20, 25)]
    assert len(set(keys)) == 5


def test_sites_separate_and_sub_grid_position_jitter_is_stable():
    first = metadata("2026-08-22T22:00:00")
    close = metadata("2026-08-22T22:00:00", (52.52001, 13.40501))
    other = metadata("2026-08-22T22:00:00", (48.137, 11.575))
    assert first == close
    assert first["session_key"] != other["session_key"]


def test_grazing_three_minute_arctic_day_is_not_lost_between_grid_points():
    # This daylight interval falls entirely between 11:55 and 12:00 scan
    # samples; locating daily extrema is necessary to discover both crossings.
    record = metadata("2026-12-21T11:58:00", (67.398, 0))
    assert record["session_kind"] == "day"
    span = (datetime.fromisoformat(record["session_end"])
            - datetime.fromisoformat(record["session_start"]))
    assert timedelta(minutes=2) < span < timedelta(minutes=5)
    assert metadata("2026-12-21T11:55:00", (67.398, 0))["session_kind"] == "night"
    assert metadata("2026-12-21T12:00:00", (67.398, 0))["session_kind"] == "night"


@pytest.mark.parametrize("date,kind", [("2026-06-21", "polar_day"), ("2026-12-21", "polar_night")])
def test_polar_phases_split_at_utc_midnight(date, kind):
    first = metadata(f"{date}T01:00:00", (89, 0))
    last = metadata(f"{date}T23:59:59.999999", (89, 0))
    following = metadata((datetime.fromisoformat(date) + timedelta(days=1, hours=1)).isoformat(), (89, 0))
    assert first == last
    assert first["session_kind"] == kind
    assert first["session_key"] != following["session_key"]
    assert first["session_start"] == f"{date}T00:00:00.000"


@pytest.mark.parametrize("stamp", [None, "", "not-a-date", "2026-13-31T20:00:00"])
def test_missing_invalid_dates_are_undated(stamp):
    assert metadata(stamp) == {"session_key": "undated", "session_kind": "undated",
                               "session_start": None, "session_end": None}


@pytest.mark.parametrize("site", [(None, None), (91, 0), (0, 181), (float('nan'), 0), (True, 0)])
def test_missing_invalid_position_remains_unassigned_by_utc_day(site):
    record = metadata("2026-08-22T23:00:00", site)
    assert record["session_key"] == "unassigned:2026-08-22"
    assert record["session_kind"] == "unassigned"
    assert metadata("2026-08-23T01:00:00", site)["session_key"] != record["session_key"]
    assert metadata("2026-08-23T01:00:00+02:00", site) == record


def test_returned_records_cannot_mutate_cached_windows():
    first = metadata("2026-08-22T22:00:00")
    expected = dict(first)
    first["session_key"] = "corrupted by caller"
    assert metadata("2026-08-22T22:00:00") == expected


def test_embedded_v8_matches_direct_authored_js():
    node = shutil.which("node")
    assert node, "Node is required for independent authored-core parity tests"
    inputs = [["2026-08-22T22:00:00", *BERLIN], ["2026-12-21T12:00:00", *BERLIN],
              ["2026-06-21T12:00:00", 89, 0], ["2026-12-21T12:00:00", 89, 0]]
    driver = Path(__file__).parent / "js" / "solar_sessions.mjs"
    result = subprocess.run([node, str(driver), json.dumps(inputs)], capture_output=True,
                            text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [solar.session_metadata(*row) for row in inputs]


@pytest.mark.parametrize("day,rise,set_", [("2026-08-22", "04:00", "18:17"),
                                          ("2026-12-21", "07:15", "14:54")])
def test_published_usno_sunrise_sunset_anchors(day, rise, set_):
    # US Naval Observatory API 4.0.1, retrieved 2026-10-11, tz=0, coordinates
    # 52.52,13.405. Independent minute-rounded standard sunrise/set values:
    # https://aa.usno.navy.mil/api/rstt/oneday?date=2026-08-22&coords=52.52%2C13.405&tz=0
    # https://aa.usno.navy.mil/api/rstt/oneday?date=2026-12-21&coords=52.52%2C13.405&tz=0
    # Two minutes permits rounding/model differences, not hour-scale regressions.
    actual = metadata(f"{day}T12:00:00")
    for field, reference in [("session_start", rise), ("session_end", set_)]:
        expected = datetime.fromisoformat(f"{day}T{reference}:00")
        assert abs((datetime.fromisoformat(actual[field]) - expected).total_seconds()) < 120


@pytest.mark.parametrize("source", ["export const x = 1;", "import x from './other.js';\nexport function f() {}",
                                    "export function f() {}\nexport { f };", "export function f() {}\nimport('./other.js');"])
def test_bridge_refuses_unrecognized_module_declarations(source):
    with pytest.raises(RuntimeError, match="Unsupported"):
        solar._adapt_module(source, {})
