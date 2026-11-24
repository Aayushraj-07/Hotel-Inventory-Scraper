"""CLI mode-selection tests (assignment §3, §8) — argument validation only.

No browser and no CSV writes: these tests stop at ``validate_args``.
"""

import pytest

from main import DEFAULT_SCENARIO, build_parser, validate_args


def parse(*argv: str):
    parser = build_parser()
    return parser, validate_args(parser, parser.parse_args(list(argv)))


def test_mode_defaults_to_live() -> None:
    _, args = parse("--config", "config/config.yaml")
    assert args.mode == "live"


@pytest.mark.parametrize("mode", ["live", "fixture", "snapshot"])
def test_all_three_modes_are_accepted(mode: str) -> None:
    _, args = parse("--mode", mode, "--input", "x.html") if mode == "snapshot" else parse("--mode", mode)
    assert args.mode == mode


def test_unknown_mode_is_rejected_by_argparse() -> None:
    with pytest.raises(SystemExit) as exc:
        parse("--mode", "stealth")
    assert exc.value.code == 2


def test_snapshot_requires_input() -> None:
    with pytest.raises(SystemExit) as exc:
        parse("--mode", "snapshot")
    assert exc.value.code == 2


def test_snapshot_rejects_fixture_only_arguments() -> None:
    with pytest.raises(SystemExit):
        parse("--mode", "snapshot", "--input", "x.html", "--scenario", "available")


def test_snapshot_rejects_malformed_checkin() -> None:
    with pytest.raises(SystemExit):
        parse("--mode", "snapshot", "--input", "x.html", "--checkin", "01-12-2026")


@pytest.mark.parametrize("extra", ["--input", "--checkin"])
def test_live_mode_rejects_snapshot_only_arguments(extra: str) -> None:
    with pytest.raises(SystemExit):
        parse("--mode", "live", extra, "x")


def test_legacy_sample_flag_forces_evidence_fixture() -> None:
    _, args = parse("--mode", "live", "--sample-from-fixture")
    assert args.mode == "fixture"
    assert args.scenario == DEFAULT_SCENARIO
    assert args.sample_from_fixture is True


def test_legacy_sample_flag_rejects_snapshot_mode() -> None:
    with pytest.raises(SystemExit) as exc:
        parse("--mode", "snapshot", "--input", "x.html", "--sample-from-fixture")
    assert exc.value.code == 2
