"""Scrubbing: the mechanical half of making a recording checkable-in.

Narrow on purpose. Absolute paths are universal and mechanical; everything
else is a human read-through, named as a step in the runbook.
"""

from __future__ import annotations

from a2a_playback.scrub import scrub_cwd

CWD = "/Users/someone/scratch/demo-app"


def test_a_tool_input_path_is_made_relative():
    play = {"tool_use": {"name": "Read", "input": {"file_path": f"{CWD}/src/app.py"}}}
    assert scrub_cwd(play, CWD) == {
        "tool_use": {"name": "Read", "input": {"file_path": "./src/app.py"}}
    }


def test_tool_output_is_scrubbed_too():
    """pytest output quotes absolute paths, and it is a plain string field."""
    play = {"tool_result": {"id": "t1", "output": f"{CWD}/tests/test_app.py .. [100%]"}}
    assert scrub_cwd(play, CWD)["tool_result"]["output"] == "./tests/test_app.py .. [100%]"


def test_a_diff_body_is_scrubbed():
    play = {"file_change": {"path": f"{CWD}/src/app.py", "diff": f"--- a{CWD}/src/app.py\n"}}
    scrubbed = scrub_cwd(play, CWD)
    assert scrubbed["file_change"]["path"] == "./src/app.py"
    assert CWD not in scrubbed["file_change"]["diff"]


def test_nested_permission_branches_are_scrubbed():
    play = {"permission": {"tool": "Bash", "on_allow": [
        {"tool_result": {"id": "t2", "output": f"ran in {CWD}"}}
    ]}}
    assert CWD not in scrub_cwd(play, CWD)["permission"]["on_allow"][0]["tool_result"]["output"]


def test_cost_and_usage_survive():
    """Realistic numbers are the point; they are not secrets."""
    play = {"result": {"cost_usd": 0.017, "usage": {"input_tokens": 10}}}
    assert scrub_cwd(play, CWD) == play


def test_the_input_is_not_mutated():
    play = {"tool_use": {"input": {"file_path": f"{CWD}/a.py"}}}
    scrub_cwd(play, CWD)
    assert play["tool_use"]["input"]["file_path"] == f"{CWD}/a.py"
