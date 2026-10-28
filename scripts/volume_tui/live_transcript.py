"""One run's transcript, folded incrementally while the run is still writing.

The GUI bridge used to rebuild a Transcript from scratch on every poll. That
was not append-only: the harness lines carry no timestamps and were spread
over the *current* model-call window, so the same event could land in a new
position (or be reported twice by the stream and the harness) and a row that
was already on screen could disappear or move. This holder keeps one
EventTail / StreamTail / Transcript per run, so each poll only folds new
evidence and the painted order never changes retroactively.

Pure bridge code: no HTTP, no Textual. `app.py` owns the process cache.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import stream as stream_mod
import trace_events as trace_mod
import transcript as transcript_mod


class LiveTranscript:
    """Incremental transcript of one run directory."""

    def __init__(
        self,
        run_dir: Path,
        task: str,
        started_at: float | None = None,
        session_dir: Path | None = None,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.transcript = transcript_mod.Transcript()
        self.transcript.add(
            transcript_mod.USER,
            title=str(task or ""),
            ts=started_at if started_at is not None else time.time(),
        )
        self.tail = trace_mod.EventTail(self.run_dir, session_dir)
        self.stream = stream_mod.StreamTail(self.run_dir)
        self._lock = threading.Lock()
        self._notices: set[str] = set()
        self._closed = False
        self.last_used = time.time()

    @property
    def blocks(self) -> list[dict]:
        return list(self.transcript.blocks)

    def set_task(self, task: str) -> None:
        """Keep the user row in step with a renamed run's config."""
        with self._lock:
            for block in self.transcript.blocks:
                if block.get("kind") == transcript_mod.USER:
                    block["title"] = str(task or "")
                    break

    def poll(self) -> list[dict]:
        """Fold every event written since the last poll; never re-read history."""
        with self._lock:
            self.last_used = time.time()
            resolved = trace_mod.resolve_session_dir(self.run_dir)
            if resolved is not None and self.tail.session_dir is None:
                # The session directory can appear a moment after the run is
                # minted; start reading its event log as soon as it exists.
                self.tail.session_dir = Path(resolved)
            try:
                fresh = self.tail.poll()
            except Exception:
                fresh = []
            try:
                streamed = self.stream.poll()
            except Exception:
                streamed = []
            # Sort only this batch: the model stream and the recorded service
            # evidence of one poll still read in execution order, while rows
            # that were already folded keep their painted position forever.
            for event in trace_mod.sort_events(streamed + fresh):
                self.transcript.ingest(event)
            return self.blocks

    def finalize(self, state: str, stopping: bool) -> list[dict]:
        """Close running rows and append the terminal notice once."""
        with self._lock:
            state = str(state or "").lower()
            if state != "running" or stopping:
                end = transcript_mod.run_end_ts(self.transcript.blocks)
                for kind in (transcript_mod.THINKING, transcript_mod.COMPACTION):
                    block = self.transcript.find(kind)
                    if block is not None and block.get("status") == "running":
                        block["status"] = "done"
                        if kind == transcript_mod.THINKING:
                            try:
                                block["subtitle"] = transcript_mod._thinking_elapsed(
                                    block, now=end
                                )
                            except Exception:
                                pass
                        if isinstance(block.get("data"), dict):
                            block["data"]["open"] = False
            notice = None
            if stopping:
                notice = (
                    "Run stopping - the launcher was asked to stop; evidence "
                    "already written stays."
                )
            elif state in ("stopped", "interrupted"):
                notice = (
                    "Run stopped - the harness and its children were terminated, "
                    "the evidence stays on disk."
                )
            elif state == "failed":
                notice = "Run failed - see failure.json in the run folder."
            if notice is not None and notice not in self._notices:
                self._notices.add(notice)
                self.transcript.add(transcript_mod.NOTICE, title=notice)
            self._closed = state != "running"
            return self.blocks
