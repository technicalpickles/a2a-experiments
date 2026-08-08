"""rig-record's argument handling.

Config problems are user errors, not crashes: say what is wrong and exit 2,
the same contract rig-serve already honors.
"""

from __future__ import annotations

import pytest

from a2a_playback.record import main


def test_out_is_required(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--backend", "echo"])
    assert exc.value.code == 2
    assert "--out" in capsys.readouterr().err


def test_writing_into_a_scenarios_directory_is_refused(tmp_path, capsys):
    """Staging is the point. A flag that could skip the scrub is one that will."""
    target = tmp_path / "repos" / "billing-api" / "scenarios" / "rec.yaml"
    with pytest.raises(SystemExit) as exc:
        main(["--backend", "echo", "--out", str(target)])
    assert exc.value.code == 2
    assert "scenarios" in capsys.readouterr().err


def test_playback_mode_requires_a_repo(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--backend", "playback", "--out", str(tmp_path / "rec.yaml")])
    assert exc.value.code == 2
    assert "--repo" in capsys.readouterr().err


def test_a_missing_repo_is_a_user_error_not_a_traceback(tmp_path, capsys):
    """RepoError is a config problem, not a bad flag — caught and reported."""
    code = main(["--backend", "playback", "--repo", str(tmp_path / "nope"),
                 "--out", str(tmp_path / "rec.yaml")])
    assert code == 2
    assert "not a repo" in capsys.readouterr().err


def test_an_unknown_backend_is_a_user_error(tmp_path, capsys):
    code = main(["--backend", "nonsense", "--out", str(tmp_path / "rec.yaml")])
    assert code == 2
    assert "nonsense" in capsys.readouterr().err
