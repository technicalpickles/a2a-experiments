"""rig-record's argument handling.

Config problems are user errors, not crashes: say what is wrong and exit 2,
the same contract rig-serve already honors.
"""

from __future__ import annotations

import argparse

import pytest

from a2a_playback.record import build_recording_backend, main


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


def test_max_budget_is_rejected_for_a_backend_with_no_ceiling(tmp_path, capsys):
    """ACPBackend takes no budget kwarg at all; a flag that silently does
    nothing is worse than one that fails the launch."""
    with pytest.raises(SystemExit) as exc:
        main([
            "--backend", "acp",
            "--out", str(tmp_path / "rec.yaml"),
            "--max-budget-usd", "5",
        ])
    assert exc.value.code == 2
    assert "no cost ceiling" in capsys.readouterr().err


# --- build_recording_backend: kwargs and label per backend ---------------------
#
# No server is started here — just the wiring from args to make_backend's
# kwargs and the provenance label. The `acp` branch is the one a paid
# recording run reaches first, and before this test it was entirely
# unexercised.


def test_build_recording_backend_wires_acp_kwargs_and_label(monkeypatch, tmp_path):
    captured = {}

    def fake_make_backend(name, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr("a2acode.backends.make_backend", fake_make_backend)

    args = argparse.Namespace(
        backend="acp", agent="claude", cwd="/repo", repo=None,
        out=str(tmp_path / "rec.yaml"), max_budget_usd=None,
    )
    backend = build_recording_backend(args)

    assert captured["name"] == "acp"
    assert captured["kwargs"] == {"agent": "claude", "cwd": "/repo"}
    assert backend._provenance["backend"] == "acp:claude"


def test_build_recording_backend_wires_claude_kwargs_and_label(monkeypatch, tmp_path):
    captured = {}

    def fake_make_backend(name, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr("a2acode.backends.make_backend", fake_make_backend)

    args = argparse.Namespace(
        backend="claude", agent="claude", cwd="/repo", repo=None,
        out=str(tmp_path / "rec.yaml"), max_budget_usd=2.5,
    )
    backend = build_recording_backend(args)

    assert captured["name"] == "claude"
    assert captured["kwargs"] == {"cwd": "/repo", "max_budget_usd": 2.5}
    assert backend._provenance["backend"] == "claude"
