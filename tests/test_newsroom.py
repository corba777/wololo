"""Newsroom pipeline end-to-end — claims to dashboard over taunts only."""

from __future__ import annotations

from pathlib import Path

from wololo.agents.tools import args_as_text
from wololo.codec import split_frames
from wololo.orchestrator.newsroom import (
    FAKE_KIND,
    SAMPLE_CLAIMS,
    VERIFIED_KIND,
    build_newsroom_pipeline,
)
from wololo.orchestrator.newsstore import (
    FileDashboardSession,
    FileDeskSession,
    read_dashboard,
    submit_claim,
)
from wololo.orchestrator.scenarios import run_scenario

TRUE_CLAIM, FAKE_CLAIM = SAMPLE_CLAIMS


def test_pipeline_publishes_and_flags() -> None:
    world = build_newsroom_pipeline()
    result = run_scenario(world.scenario)
    assert result.reached, f"dashboard incomplete after {result.ticks} ticks"
    assert world.dashboard.news == [
        {"headline": f"Confirmed: {TRUE_CLAIM.rstrip('.')}", "body": TRUE_CLAIM}
    ]
    assert world.dashboard.fakes == [FAKE_CLAIM]
    assert result.supervisor.restarts == {0: 0, 1: 0}


def test_claims_crossed_the_taunt_channel() -> None:
    """The data plane is the codec: claim text and verdicts are in chat."""
    world = build_newsroom_pipeline()
    result = run_scenario(world.scenario)
    checker_taunts = [e.taunt for e in result.kernel.taunt_log if e.sender == 0]
    messages, remainder = split_frames(checker_taunts)
    assert remainder == []
    assert [(m.kind, args_as_text(m.args)) for m in messages] == [
        (VERIFIED_KIND, TRUE_CLAIM),
        (FAKE_KIND, FAKE_CLAIM),
    ]


def test_pipeline_is_deterministic() -> None:
    def fingerprint() -> tuple:
        world = build_newsroom_pipeline(seed=3)
        result = run_scenario(world.scenario)
        log = tuple((e.tick, e.sender, e.taunt) for e in result.kernel.taunt_log)
        return (result.reached, result.ticks, tuple(world.dashboard.fakes), log)

    assert fingerprint() == fingerprint()


def test_file_stores_round_trip(tmp_path: Path) -> None:
    """The Streamlit seam: file-backed sessions behave like the fakes."""
    submit_claim(tmp_path, TRUE_CLAIM)
    submit_claim(tmp_path, FAKE_CLAIM)
    world = build_newsroom_pipeline(
        desk_session=FileDeskSession(tmp_path),
        dashboard_session=FileDashboardSession(tmp_path),
        log_directory=tmp_path,
    )
    result = run_scenario(world.scenario)
    assert result.reached
    news, fakes = read_dashboard(tmp_path)
    assert [n["body"] for n in news] == [TRUE_CLAIM]
    assert fakes == [FAKE_CLAIM]

    from wololo.orchestrator.newsstore import read_log

    events = [e["event"] for e in read_log(tmp_path)]
    assert "journalist_heard" in events
    assert "journalist_received" in events
    assert "journalist_writing" in events
    assert "published" in events
    assert "fake_flagged" in events

    from wololo.orchestrator.newsroom import (
        JOURNALIST_ACK_FAKE,
        JOURNALIST_ACK_HEARD,
        JOURNALIST_ACK_PUBLISH,
        JOURNALIST_ACK_RECEIVED,
    )

    agent1_taunts = [e.taunt for e in result.kernel.taunt_log if e.sender == 1]
    assert JOURNALIST_ACK_HEARD in agent1_taunts
    assert JOURNALIST_ACK_RECEIVED in agent1_taunts
    assert JOURNALIST_ACK_PUBLISH in agent1_taunts
    assert JOURNALIST_ACK_FAKE in agent1_taunts


def test_log_and_status(tmp_path: Path) -> None:
    from wololo.orchestrator.newsstore import (
        append_log,
        clear_log,
        log_for_display,
        read_log,
        read_status,
    )

    submit_claim(tmp_path, "hello")
    append_log(tmp_path, "tick", epoch=1, taunts=0)
    log = read_log(tmp_path)
    assert log[0]["event"] == "claim_submitted"
    assert log[1]["event"] == "tick"
    status = read_status(tmp_path)
    assert status["inbox_pending"] == 1
    assert status["news_count"] == 0
    assert status["last_log_ts"] is not None

    append_log(tmp_path, "runner_start", mode="sim", llm=False)
    append_log(tmp_path, "tick", epoch=2, taunts=0)
    append_log(tmp_path, "runner_stop")
    assert log_for_display(read_log(tmp_path), tmp_path) == []
    clear_log(tmp_path)
    assert read_log(tmp_path) == []

    append_log(tmp_path, "runner_start", mode="sim", llm=False)
    shown = log_for_display(read_log(tmp_path), tmp_path)
    assert shown and shown[0]["event"] == "runner_start"


def test_clear_dashboard(tmp_path: Path) -> None:
    from wololo.orchestrator.newsstore import clear_dashboard

    dash = FileDashboardSession(tmp_path)
    dash.call_tool("publish_news", {"headline": "Headline", "body": "Body"})
    dash.call_tool("flag_fake", {"claim": "nope"})
    news, fakes = read_dashboard(tmp_path)
    assert news and fakes
    clear_dashboard(tmp_path)
    assert read_dashboard(tmp_path) == ([], [])


def test_desk_cursor_sees_only_new_claims(tmp_path: Path) -> None:
    desk = FileDeskSession(tmp_path)
    assert desk.call_tool("fetch_claims", {}) == '{"claims": []}'
    submit_claim(tmp_path, "one")
    assert "one" in desk.call_tool("fetch_claims", {})
    assert desk.call_tool("fetch_claims", {}) == '{"claims": []}'  # consumed
    submit_claim(tmp_path, "two")
    assert "two" in desk.call_tool("fetch_claims", {})


def test_desk_cursor_persists_across_sessions(tmp_path: Path) -> None:
    submit_claim(tmp_path, "once")
    FileDeskSession(tmp_path).call_tool("fetch_claims", {})
    again = FileDeskSession(tmp_path)
    assert again.call_tool("fetch_claims", {}) == '{"claims": []}'


def test_runner_pid_and_stop(tmp_path: Path) -> None:
    import os

    from wololo.orchestrator.newsstore import (
        clear_runner_stop,
        read_runner_pid,
        runner_is_alive,
        stop_requested,
        write_runner_pid,
    )

    write_runner_pid(tmp_path, os.getpid())
    assert read_runner_pid(tmp_path) == os.getpid()
    assert runner_is_alive(tmp_path)
    (tmp_path / "runner.stop").touch()
    assert stop_requested(tmp_path)
    clear_runner_stop(tmp_path)
    assert not stop_requested(tmp_path)
