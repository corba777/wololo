"""Streamlit front end for the newsroom pipeline.

The reader's side of the demo: a form that drops claims into the inbox
file, and a dashboard that renders what the journalist agent published.
The agents themselves run in a separate process (`scripts/newsroom_demo.py`)
and talk to each other exclusively over the taunt channel.

Run:
    streamlit run apps/newsroom_app.py
    streamlit run apps/newsroom_app.py -- --dir /custom/exchange/dir
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import streamlit as st

# Allow running from a repo checkout without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from wololo.orchestrator.newsstore import (
    clear_dashboard,
    clear_log,
    log_for_display,
    read_dashboard,
    read_log,
    read_runner_pid,
    request_runner_stop,
    runner_is_alive,
    submit_claim,
)

DEFAULT_DIR = Path.home() / ".wololo" / "newsroom"


def exchange_dir() -> Path:
    args = sys.argv[1:]
    if "--dir" in args:
        return Path(args[args.index("--dir") + 1]).expanduser()
    return DEFAULT_DIR


def format_log_line(entry: dict[str, Any]) -> str:
    ts = entry.get("ts", "")[-8:]  # HH:MM:SS from ISO timestamp
    event = entry.get("event", "?")
    match event:
        case "claim_submitted":
            return f"{ts}  📥 submitted: {entry.get('text', '')}"
        case "claim_fetched":
            claims = entry.get("claims") or []
            return f"{ts}  🔍 fact checker picked up: {'; '.join(claims)}"
        case "taunt_burst":
            return (
                f"{ts}  📣 agent {entry['agent']} shouted "
                f"{entry['count']} taunts (epoch {entry['epoch']}, "
                f"{entry['first']}…{entry['last']})"
            )
        case "journalist_heard":
            return (
                f"{ts}  📨 agent 1 heard {entry.get('count', '?')} taunts "
                f"from fact checker (epoch {entry.get('epoch', '?')})"
            )
        case "journalist_decoding":
            return f"{ts}  🔤 agent 1 decoding ({entry.get('taunts', '?')} taunts)…"
        case "journalist_received":
            verdict = "✅ verified" if entry.get("verdict") == "verified" else "🚫 fake"
            return f"{ts}  📬 agent 1 received claim [{verdict}]: {entry.get('text', '')}"
        case "journalist_writing":
            if entry.get("action") == "flag":
                return f"{ts}  ✍️ agent 1 debunking: {entry.get('text', '')}"
            return f"{ts}  ✍️ agent 1 writing story: {entry.get('text', '')}"
        case "taunt":
            return (
                f"{ts}  📣 agent {entry['agent']} taunt {entry['taunt']} "
                f"(epoch {entry.get('epoch', '?')})"
            )
        case "tick":
            return f"{ts}  ⏳ epoch {entry.get('epoch', '?')} — no taunts this turn"
        case "published":
            return f"{ts}  ✅ published: {entry.get('headline', '')}"
        case "fake_flagged":
            return f"{ts}  🚫 fake news: {entry.get('claim', '')}"
        case "runner_start":
            llm = f", model={entry['model']}" if entry.get("llm") else ""
            return f"{ts}  🚀 runner started (mode={entry.get('mode')}{llm})"
        case "de_connected":
            return f"{ts}  🎮 connected to AoE II DE bridge"
        case "de_timeout":
            return f"{ts}  ⚠️ game timeout: {entry.get('message', '')}"
        case "runner_stop":
            return f"{ts}  🛑 runner stopped"
        case _:
            return f"{ts}  {event}: {entry}"


st.set_page_config(page_title="wololo newsroom", page_icon="📰", layout="wide")
st.title("📰 wololo newsroom")
st.caption(
    "Claims go to the fact-checker agent; verdict and text cross to the "
    "journalist agent over Age of Empires II taunts; the journalist "
    "publishes here."
)

directory = exchange_dir()

status_col, stop_col = st.columns([5, 1])
with status_col:
    if runner_is_alive(directory):
        pid = read_runner_pid(directory)
        st.success(f"Runner active (pid {pid})")
    else:
        st.caption("Runner not running — start `python scripts/newsroom_demo.py --de --llm`")
with stop_col:
    if st.button("Stop runner", disabled=not runner_is_alive(directory)):
        request_runner_stop(directory)
        st.toast("Stop signal sent to the runner.")
        time.sleep(0.5)
        st.rerun()

with st.form("submit", clear_on_submit=True):
    claim = st.text_input("Submit a claim", placeholder="e.g. Honey never spoils.")
    if st.form_submit_button("Send to the newsroom") and claim.strip():
        submit_claim(directory, claim.strip())
        st.toast("Claim submitted — the fact checker will pick it up.")


@st.fragment(run_every=2.0)
def live_view() -> None:
    log = log_for_display(read_log(directory, limit=100), directory)

    head, _, clear = st.columns([6, 1, 1])
    with head:
        st.subheader("Verbose log")
    if clear.button("Clear log", key="clear_log"):
        clear_log(directory)
        st.rerun(scope="fragment")

    st.caption(f"Exchange directory: `{directory}`")
    if not log:
        st.info(
            "No activity yet. Start the agents in another terminal:\n\n"
            "`python scripts/newsroom_demo.py --de --llm`"
        )
    else:
        lines = "\n".join(format_log_line(e) for e in reversed(log))
        st.code(lines, language=None)

    dash_head, _, clear_news = st.columns([6, 1, 1])
    with dash_head:
        st.subheader("Results")
    if clear_news.button("Clear news", key="clear_news"):
        clear_dashboard(directory)
        st.rerun(scope="fragment")

    verified_col, fake_col = st.columns(2)
    news, fakes = read_dashboard(directory)
    with verified_col:
        st.markdown("**✅ Verified news**")
        if not news:
            st.caption("Nothing published yet.")
        for story in reversed(news):
            with st.container(border=True):
                st.markdown(f"**{story['headline']}**")
                st.write(story["body"])
    with fake_col:
        st.markdown("**🚫 Fake News**")
        if not fakes:
            st.caption("Nothing debunked yet.")
        for item in reversed(fakes):
            st.error(item)


live_view()
