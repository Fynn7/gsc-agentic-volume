"""Stdlib interactive TUI (Windows PowerShell 5.1 / Windows Terminal safe).

No third-party dependency: Textual was evaluated (see README) but is not
installed here and would require a network install on first run, blocking
research use. This fallback uses only ANSI + msvcrt, keeps one bottom
shortcut bar, and never prints big JSON/base64/full logs on the main screen.
If `textual` becomes available in the isolated env, a richer frontend may
replace this module without touching launcher semantics.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as cfg
import env as env_mod
import runs as runs_mod
import runner as runner_mod
import screen as screen_mod
import services as services_mod
import trace_events as trace_mod

SHORTCUTS = "Up/Down select  Enter open  Esc back  i image  d details  o folder  q quit"
# Shared loading tick: one animation family everywhere (menu/live/startup/verify).
TICK = 0.12


def _clear():
    # Legacy one-shot clear. Live loops must NOT use this per frame (it
    # flashes); they paint in place via screen_mod.Screen instead.
    os.system("cls" if os.name == "nt" else "clear")


def _read_key():
    """Return 'up','down','enter','esc' or a single char. Windows arrows via msvcrt."""
    if os.name == "nt":
        try:
            import msvcrt
        except ImportError:
            return input("> ").strip().lower()
        while True:
            ch = msvcrt.getch()
            if ch in (b"\x00", b"\xe0"):
                ch2 = msvcrt.getch()
                return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(ch2.decode("latin1"), "")
            if ch == b"\r":
                return "enter"
            if ch == b"\x1b":
                return "esc"
            try:
                return ch.decode("utf-8", "ignore").lower()
            except Exception:
                return ""
    return input("number + Enter (b=back): ").strip().lower()


def _cat_title(event: dict) -> str:
    """Event title in its category color (warnings orange, errors red)."""
    title = event["title"][:110]
    color = screen_mod.CATEGORY_COLORS.get(event.get("cat", ""))
    if event.get("cat") == "ERROR" and title.startswith("WARN"):
        color = "orange"
    return screen_mod.colored(title, color) if color else title


def _color_status(status: str) -> str:
    """Finished green / Failed red / Running cyan / Unknown gray."""
    return screen_mod.colored(status, {
        "Finished": "green", "Failed": "red", "Running": "cyan",
        "Unknown": "gray", "finished": "green", "failed": "red",
    }.get(status, "")) if status else status


def _menu(title: str, lines: list[str], options: list[str], start: int = 0) -> int | None:
    """Arrow menu. Returns index or None on Esc. POSIX fallback: numeric input."""
    if os.name != "nt":
        _clear()
        print(title + "\n" + "=" * 60)
        for l in lines:
            print(l)
        print("-" * 60)
        for i, o in enumerate(options):
            print("  %d) %s" % (i + 1, o))
        print("\n" + SHORTCUTS)
        raw = input("> ").strip().lower()
        if raw in ("b", "q", ""):
            return None
        try:
            idx = int(raw) - 1
            return idx if 0 <= idx < len(options) else None
        except ValueError:
            return None
    idx = start
    import msvcrt  # noqa: F401
    frame = 0
    with screen_mod.Screen() as scr:
        while True:
            rows = [title, "=" * 70, *lines, "-" * 70]
            for i, o in enumerate(options):
                rows.append(screen_mod.colored("> " + o, "bright_cyan") if i == idx else "  " + o)
            rows += ["-" * 70, SHORTCUTS]
            scr.draw(rows)
            key = _read_key()
            frame += 1
            if key == "up":
                idx = (idx - 1) % len(options)
            elif key == "down":
                idx = (idx + 1) % len(options)
            elif key == "enter":
                return idx
            elif key == "esc":
                return None
            elif key and key.isdigit():
                n = int(key) - 1
                if 0 <= n < len(options):
                    return n


def _pause(msg="Enter=continue Esc=back"):
    print("\n[%s]" % msg)
    key = _read_key()
    return key


def _open_path(path: str):
    p = Path(path)
    if not p.exists():
        print("Missing (not yet written or deleted): %s" % path)
        _pause()
        return
    try:
        if hasattr(os, "startfile"):
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(p)])
        else:
            subprocess.run(["xdg-open", str(p)])
        print("Opened: %s" % p)
    except Exception as error:
        print("Viewer did not open: %s" % error)
    _pause()


def _status_lines(settings: dict) -> list[str]:
    # Cached: a redraw must never block on HTTP timeouts; Running lags <=5s by design.
    checks = env_mod.cached_check(settings)["items"]
    get = lambda name: next((c for c in checks if c["name"].startswith(name)), {})
    runs = runs_mod.discover_runs(limit=50)
    running = [r for r in runs if r["status"] == "Running"]
    last = runs[0] if runs else None
    inference = get("Inference")
    renderer = get("Renderer")
    return [
        "Mode: %s   Model: %s" % (settings.get("mode"), settings.get("model")),
        screen_mod.colored("Inference: %s" % ("OK" if inference.get("ok") else "DOWN: %s" % inference.get("detail", "")[:80]),
                           "green" if inference.get("ok") else "red"),
        screen_mod.colored("Renderer:  %s" % ("OK" if renderer.get("ok") else "DOWN: %s" % renderer.get("detail", "")[:80]),
                           "green" if renderer.get("ok") else "red"),
        "Services: %s" % services_mod.owned_summary(),
        "Running now: %s" % (running[0]["runId"] if running else "none"),
        "Last run: %s (%s)" % (last["runId"] if last else "none",
                               _color_status(last["status"]) if last else "-"),
    ]


def _service_gate(settings: dict) -> list[dict]:
    """Fresh (uncached) Inference+Renderer+model check; blocks doomed runs before any side effect."""
    items = screen_mod.run_with_spinner(
        "Checking inference + renderer + model",
        lambda: env_mod.check_environment(settings))["items"]
    down = [i for i in items if i["name"].startswith(("Inference", "Renderer")) and not i["ok"]]
    try:
        down += screen_mod.run_with_spinner(
            "Checking required model", services_mod.model_problems, settings)
    except Exception:
        pass
    return down


def _print_problem(item: dict):
    """One DOWN item: name red, fix orange. Direct print (one-shot screens)."""
    print(screen_mod.colored("[DOWN] %s" % item["name"], "red"))
    print("       %s" % (item.get("detail", "")[:160]))
    if item.get("fix"):
        print(screen_mod.colored("       fix: %s" % item["fix"], "orange"))


def _print_check(item: dict):
    """One preflight item: OK green, !! red, fix orange."""
    mark = "OK" if item["ok"] else "!!"
    print("[%s] %s %s" % (screen_mod.colored(mark, "green" if item["ok"] else "red"),
                          item["name"], (item.get("detail", "") or "")[:100]))
    if not item["ok"] and item.get("fix"):
        print(screen_mod.colored("     fix: %s" % item["fix"], "orange"))


def _print_verify_line(line: str):
    """Verify summary: passed green, NOT passed red, UNVERIFIED orange."""
    if "NOT passed" in line:
        print(screen_mod.colored("- " + line, "red"))
    elif "UNVERIFIED" in line:
        print(screen_mod.colored("- " + line, "orange"))
    elif "passed" in line:
        print(screen_mod.colored("- " + line, "green"))
    else:
        print("- " + line)


def _failure_hint(run_dir: Path) -> str | None:
    """One-line cause for a failed run from its own failure.json/run.json (read-only)."""
    for name in ("failure.json", "run.json"):
        try:
            data = json.loads((run_dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        error = data.get("error")
        if error:
            return "cause: %s" % " ".join(str(error).split())[:200]
    return None


def _temporary_context_offer(settings: dict, problems: list[dict]) -> dict | None:
    """Return the single Model-context blocker if a temporary instance can cover it, else None.

    Pure predicate (testable): Local mode, exactly one problem, and it is the
    context mismatch — meaning the user's Ollama is reachable but loaded with
    a different context length. Anything else (Inference/Renderer DOWN, model
    missing) still needs a manual fix.
    """
    if settings.get("mode") != "Local" or len(problems) != 1:
        return None
    only = problems[0]
    return only if only.get("name") == "Model context" else None


def _confirm_and_run(task_kind: str, task_label: str, settings: dict):
    try:
        effective = cfg.build_effective_config(task_kind, "", settings) if task_kind != "custom" else None
    except ValueError as error:
        print(str(error))
        _pause()
        return
    custom_text = ""
    if task_kind == "custom":
        _clear()
        print("Custom task (single input, Enter twice to finish):\n- Uses the same MCP/Renderer/CLI flow.\n- Never inherits Task A/B prompts.\n- Budget/context match the example config.")
        print("-" * 70)
        lines = []
        print("(type lines, empty line to finish)")
        while True:
            try:
                line = input("> ")
            except EOFError:
                break
            if not line.strip():
                break
            lines.append(line)
        custom_text = "\n".join(lines).strip()
        if not custom_text:
            print("Empty task; cancelled.")
            _pause()
            return
        try:
            effective = cfg.build_effective_config("custom", custom_text, settings)
        except ValueError as error:
            print(str(error))
            _pause()
            return
    assert effective is not None
    _clear()
    print("Confirm run (one confirmation creates exactly one independent Run)\n" + "=" * 70)
    print("Task:    %s" % task_label)
    print("Model:   %s" % effective["model"])
    print("Volume:  %s" % effective["volumePath"])
    print("Budget:  %s renders" % effective["renderBudget"])
    print("Mode:    %s (%s)" % (settings["mode"], effective["endpoint"]))
    print("Saves:   new run under %s (original config never overwritten)" % effective["resultDirectory"])
    print("Prompt:  %s" % effective["task"][:300])
    print("-" * 70 + "\n" + SHORTCUTS)
    choice = _menu("Start this run?", [], ["Confirm and run", "Cancel"])
    if choice != 0:
        return
    problems = _service_gate(settings)
    if problems:
        remediable = _temporary_context_offer(settings, problems)
        if remediable is not None and effective is not None:
            want = cfg.example_config().get("contextSize", 16384)
            choice = _menu("Context mismatch — start a temporary instance?",
                           ["Your Ollama is reachable but loaded context %s." % remediable.get("detail", ""),
                            "Temporary: a second TUI-owned Ollama (context %s) on a free 127.0.0.1 port," % want,
                            "used ONLY by this run; your Ollama and saved settings stay untouched.",
                            "It stops automatically when this TUI exits. Needs free VRAM for one more load.",
                            "Manual: restart your own Ollama with OLLAMA_CONTEXT_LENGTH=%s." % want],
                           ["Start temporary instance for this run", "Back (I will fix it manually)"])
            if choice == 0:
                started = screen_mod.run_with_spinner(
                    "Starting temporary Ollama (context %s)" % want,
                    services_mod.start_temporary_ollama, settings, want)
                if started.get("ok"):
                    effective["endpoint"] = started["endpoint"]
                    print("Temporary inference for this run only: %s (pid %s; stops on TUI exit)."
                          % (started["endpoint"], started["pid"]))
                    shadow = dict(settings, endpoint=started["endpoint"])
                    problems = _service_gate(shadow)
                else:
                    _clear()
                    print("Temporary instance failed: %s" % started.get("error"))
                    print("Fix your Ollama manually, then start a new run. Nothing was written.")
                    _pause("Enter=back to menu")
                    return
        if problems:
            _clear()
            print("Run BLOCKED: required services are DOWN (no config written, no run created).")
            for item in problems:
                _print_problem(item)
            _pause("Enter=back to menu")
            return
    config_path = cfg.write_run_config(effective, task_kind)
    print("Config: %s\nLaunching existing launcher (no second agent loop)..." % config_path)
    live_lines: list[str] = []
    lock = threading.Lock()
    box: dict = {}

    def on_line(text: str):
        with lock:
            live_lines.append(text)

    def work():
        try:
            box["result"] = runner_mod.launch(config_path, on_line=on_line)
        except Exception as error:
            box["error"] = str(error)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    # Wait until run_started or launcher exit (flicker-free + spinner).
    run_dir: Path | None = None
    start_ts = time.time()
    frame = 0
    with screen_mod.Screen() as scr:
        while thread.is_alive():
            time.sleep(TICK)
            with lock:
                for raw in list(live_lines):
                    try:
                        payload = json.loads(raw)
                    except ValueError:
                        continue
                    if payload.get("event") == "run_started" and payload.get("directory"):
                        run_dir = Path(payload["directory"])
                        break
            if run_dir is not None:
                break
            with lock:
                tail_log = list(live_lines[-8:])
            scr.draw(
                ["%s Starting... (%.0fs) (duplicate-task launches are refused with exit 3, nothing killed)"
                 % (screen_mod.spinner(frame), time.time() - start_ts),
                 *["  " + raw[:160] for raw in tail_log],
                 "",
                 "Waiting for run_started... (Esc aborts VIEW only, agent keeps running)"]
            )
            frame += 1
    if run_dir is None and "result" in box and getattr(box["result"], "directory", None):
        run_dir = box["result"].directory
    if run_dir is None:
        _clear()
        print("Launch failed before reserving a run (config/duplicate/reservation). No evidence touched.")
        with lock:
            print("".join(live_lines)[-3000:])
        _pause()
        return
    _live_view(run_dir)
    thread.join(timeout=1)


def _live_view(run_dir: Path):
    """Real-time trace: poll EventTail, show Tool Name -> Args -> Result -> Duration -> Image.

    Once the run reaches finished/failed the view FREEZES: no more polling,
    no growing wait timer, no spinner animation. Only user actions (scroll,
    details, image, verify, back) repaint once.
    """
    session_dir = trace_mod.resolve_session_dir(run_dir)
    tail = trace_mod.EventTail(run_dir, session_dir)
    shown: list[dict] = []
    detail_on = True
    offset = 0  # 0 = live tail; Up scrolls back, Down returns toward the tail.
    start_ts = time.time()
    last_event_ts = start_ts
    frame = 0
    done_at: float | None = None  # latched the moment finished/failed is seen

    def _frame_lines(run_record: dict, finished: bool) -> list[str]:
        status = run_record.get("status", "?")
        if finished:
            total = (done_at if done_at is not None else time.time()) - start_ts
            head = screen_mod.colored(
                ("Live trace: %s  [%s] ■ done in %.0fs  events=%d (frozen — v=verify, Esc=back)%s" % (
                    run_dir.name, status, total, len(shown),
                    ("  scroll +%d (Down returns to tail)" % offset) if offset else "")),
                "green")
        else:
            state_mark = "%s working %s" % (
                screen_mod.spinner(frame), screen_mod.dots(frame))
            head = ("Live trace: %s  [%s] %s  events=%d  wait=%.0fs%s" % (
                run_dir.name, status, state_mark, len(shown),
                time.time() - last_event_ts,
                ("  scroll +%d (Down returns to tail)" % offset) if offset else ""))
        body = [head, "=" * 70]
        end = max(0, len(shown) - offset)
        window = shown[max(0, end - 18):end]
        for event in window:
            title = _cat_title(event)
            # In-flight steps keep their own spinner so waits are visible per line.
            if window and not finished and event is window[-1] and event.get("cat") in ("MODEL", "INFER", "TOOL"):
                title = "%s %s" % (screen_mod.spinner(frame), title)
            body.append("  " + title)
            if detail_on and event.get("detail"):
                for dl in event["detail"].split("\n")[:10]:
                    body.append(screen_mod.colored("      %s" % dl[:160], "gray"))
            if event.get("image"):
                body.append(screen_mod.colored("      [Open image: i] rev=%s" % event.get("revision"), "green"))
        body.append("-" * 70)
        body.append("i=image (choose)  d=details  Up/Down=scroll  o=run folder  Esc=%s" % ("back" if finished else "close (agent keeps running)"))
        if finished:
            body.append("Run %s (exit kept in manifest.json). Press v=verify, Esc=back." % run_record.get("status"))
            if run_record.get("status") == "failed":
                hint = _failure_hint(run_dir)
                body.append(screen_mod.colored("FAILED %s%s" % (hint or "cause unknown — open the run folder and read failure.json",
                                             " (0 events: the harness never started)" if not shown else ""), "red"))
        else:
            pass
        return body

    def _read_status() -> dict:
        try:
            return json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _latch_done() -> dict:
        """Final drain of trailing events, then freeze the clock."""
        nonlocal done_at, last_event_ts
        run_record = _read_status()
        # Catch the FINISH/IMAGE lines that land just after run.json flips.
        for _ in range(3):
            time.sleep(0.15)
            extra = tail.poll()
            if extra:
                shown.extend(extra)
                last_event_ts = time.time()
        done_at = time.time()
        return _read_status()

    def _frozen_loop(scr, run_record: dict):
        """Static view: block on keys, repaint only on user action. No polling."""
        nonlocal offset, detail_on
        scr.draw(_frame_lines(run_record, True))
        while True:
            if os.name != "nt":
                raw = input("[i/d/o/v/u(p)/n(ewer)/Esc]> ").strip().lower()
                if raw == "i":
                    _open_selected_image(shown)
                elif raw == "d":
                    detail_on = not detail_on
                elif raw == "u":
                    offset = offset + 5
                elif raw == "n":
                    offset = max(0, offset - 5)
                elif raw == "o":
                    _open_path(str(run_dir))
                elif raw == "v":
                    _verify_view(str(run_dir))
                    scr.draw(_frame_lines(run_record, True))
                    continue
                elif raw in ("", "b", "q"):
                    break
                else:
                    continue
                scr.draw(_frame_lines(run_record, True))
                continue
            import msvcrt
            ch = msvcrt.getch()
            if ch in (b"\x00", b"\xe0"):
                ch2 = msvcrt.getch()
                if ch2 == b"H":
                    offset = offset + 5
                elif ch2 == b"P":
                    offset = max(0, offset - 5)
                else:
                    continue
            elif ch == b"\x1b":
                break
            else:
                try:
                    pressed = ch.decode("utf-8", "ignore").lower()
                except Exception:
                    continue
                if pressed == "d":
                    detail_on = not detail_on
                elif pressed == "i":
                    _open_selected_image(shown)
                elif pressed == "o":
                    _open_path(str(run_dir))
                elif pressed == "v":
                    _verify_view(str(run_dir))
                else:
                    continue
            scr.draw(_frame_lines(run_record, True))

    with screen_mod.Screen() as scr:
        while True:
            # Re-resolve session dir once the launcher writes session.json.
            if tail.session_dir is None:
                found = trace_mod.resolve_session_dir(run_dir)
                if found:
                    tail.session_dir = found
            fresh = tail.poll()
            if fresh:
                shown.extend(fresh)
                last_event_ts = time.time()
            run_record = _read_status()
            if run_record.get("status") in ("finished", "failed"):
                run_record = _latch_done()
                _frozen_loop(scr, run_record)
                break
            scr.draw(_frame_lines(run_record, False))
            frame += 1
            if os.name != "nt":
                raw = input("[i/d/o/v/u(p)/n(ewer)/Esc]> ").strip().lower()
                if raw == "i":
                    _open_selected_image(shown)
                elif raw == "d":
                    detail_on = not detail_on
                elif raw == "u":
                    offset = offset + 5
                elif raw == "n":
                    offset = max(0, offset - 5)
                elif raw == "o":
                    _open_path(str(run_dir))
                elif raw in ("", "b", "q"):
                    break
                continue
            # Windows non-blocking poll: repaint at ~8fps with spinner, no cls.
            import msvcrt
            deadline = time.time() + 1.0
            pressed = ""
            while time.time() < deadline:
                if msvcrt.kbhit():
                    ch = msvcrt.getch()
                    if ch in (b"\x00", b"\xe0"):
                        ch2 = msvcrt.getch()
                        if ch2 == b"H":
                            pressed = "scroll-up"
                        elif ch2 == b"P":
                            pressed = "scroll-down"
                        else:
                            continue
                        break
                    if ch == b"\x1b":
                        pressed = "esc"
                        break
                    try:
                        pressed = ch.decode("utf-8", "ignore").lower()
                    except Exception:
                        pressed = ""
                    break
                time.sleep(TICK)
                # Animate the wait even with zero new events (no full clear).
                fresh = tail.poll()
                if fresh:
                    shown.extend(fresh)
                    last_event_ts = time.time()
                live_record = _read_status()
                if live_record.get("status") in ("finished", "failed"):
                    run_record = _latch_done()
                    _frozen_loop(scr, run_record)
                    return
                scr.draw(_frame_lines(live_record, False))
                frame += 1
            if pressed == "esc":
                break
            elif pressed == "scroll-up":
                offset = offset + 5
            elif pressed == "scroll-down":
                offset = max(0, offset - 5)
            elif pressed == "d":
                detail_on = not detail_on
            elif pressed == "i":
                _open_selected_image(shown)
            elif pressed == "o":
                _open_path(str(run_dir))


def _open_selected_image(shown: list[dict]):
    """Chooser over proven IMAGE events; each image sits under its producing TOOL call."""
    images = [e for e in shown if e.get("image")]
    if not images:
        print("No real PNG yet (only proven IMAGE events can be opened).")
        _pause()
        return
    recent = images[-20:]
    options = ["rev=%s  %s" % (e.get("revision"), e.get("image")) for e in recent] + ["Back"]
    choice = _menu("Proven images (each was produced by the TOOL event just above it)",
                   ["%d image(s) total; read-only, newest last." % len(images)], options)
    if choice is None or choice >= len(recent):
        return
    picked = recent[choice]
    print("IMAGE rev=%s\n%s" % (picked.get("revision"), picked.get("image")))
    _open_path(str(picked["image"]))


def _history():
    while True:
        found = runs_mod.discover_runs()
        lines = ["%d real run(s) found (read-only replay; never re-invokes model)." % len(found)]
        options = ["%s  %s  %s  frames=%s  %s" % (r["runId"], _color_status(r["status"]), r["model"], r["frames"], r.get("task", "?")) for r in found]
        options.append("Back")
        choice = _menu("History", lines, options or ["Back"])
        if choice is None or choice >= len(found):
            return
        _history_detail(found[choice])


def _history_detail(item: dict):
    run_dir = Path(item["directory"])
    while True:
        verification = None
        try:
            verification = json.loads((run_dir / "verification.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        manifest = {}
        try:
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        info = ["run=%s status=%s model=%s" % (item["runId"], item["status"], item["model"]),
                "dir=%s" % run_dir,
                "session=%s exit=%s state=%s" % (manifest.get("sessionId"), manifest.get("exitCode"), manifest.get("state")),
                "verification: %s" % (screen_mod.colored("present", "green") if verification
                                            else screen_mod.colored("missing — press Verify (no model call)", "orange"))]
        choice = _menu("Run detail (read-only)", info,
                       ["Replay events", "Verify now", "Open final PNG", "Open run folder", "Back"])
        if choice is None or choice == 4:
            return
        if choice == 0:
            _replay_view(run_dir)
        elif choice == 1:
            _verify_view(str(run_dir))
        elif choice == 2:
            final = (verification or {}).get("final", {}) if verification else {}
            path = final.get("image") if final else None
            if not path:
                # Fallback: newest frame PNG under runtime/<session>.
                session_id = manifest.get("sessionId") or item.get("sessionId")
                candidates = sorted((ROOT / "runtime" / str(session_id)).rglob("*.png")) if session_id else []
                path = str(candidates[-1]) if candidates else None
            if path:
                _open_path(path)
            else:
                print("No final PNG evidence.")
                _pause()
        elif choice == 3:
            _open_path(str(run_dir))


def _replay_view(run_dir: Path):
    session_dir = trace_mod.resolve_session_dir(run_dir)
    tail = trace_mod.EventTail(run_dir, session_dir)
    shown: list[dict] = []
    previous = -1
    while True:  # drain incremental reader like agent_console replay
        shown += tail.poll()
        if len(shown) == previous:
            break
        previous = len(shown)
    detail = True
    _clear()
    print("Replay (read-only, %d events): %s" % (len(shown), run_dir.name))
    print("=" * 70)
    for event in shown[-60:]:
        print(event["title"][:110])
        if detail and event.get("detail"):
            for dl in event["detail"].split("\n")[:3]:
                print("      %s" % dl[:110])
    print("-" * 70)
    choice = _menu("Replay actions", ["Full list above (capped at 60). Images open read-only."],
                   ["Open an image", "Open run folder", "Back"])
    if choice == 0:
        _open_selected_image(shown)
    elif choice == 1:
        _open_path(str(run_dir))


def _verify_view(run_dir: str):
    _clear()
    hint = _failure_hint(Path(run_dir))
    if hint:
        print(screen_mod.colored("Run FAILED: %s" % hint, "red"))
    if not (Path(run_dir) / "session.json").exists():
        print("Nothing to verify: no session.json (the harness never started).")
        print("Fix the cause above, then start a new run. This evidence is kept as-is.")
        _pause()
        return
    result = screen_mod.run_with_spinner(
        "Verifying (existing verifier only; no model call)",
        runs_mod.run_verify, run_dir)
    print("exit=%s" % result["code"])
    for line in runs_mod.verify_summary(result["report"]):
        _print_verify_line(line)
    print("\nFull report kept at %s" % (Path(run_dir) / "verification.json"))
    _pause()


def _settings(settings: dict) -> dict:
    while True:
        lines = ["mode=%s model=%s" % (settings["mode"], settings["model"]),
                 "local endpoint=%s" % settings["endpoint"],
                 "h200 endpoint=%s (tunnel; never auto-guessed)" % settings["h200Endpoint"],
                 "renderer=%s" % settings["rendererService"],
                 "H200 status: PENDING field verification (never shown as verified without a live check)."]
        choice = _menu("Settings (saved for next launch; each run still stores its full config)",
                       lines, ["Toggle Local/H200", "Edit model", "Edit endpoints", "Check H200 now", "Back"])
        if choice is None or choice == 4:
            return settings
        if choice == 0:
            settings["mode"] = "H200" if settings["mode"] == "Local" else "Local"
            cfg.save_settings(settings)
        elif choice == 1:
            _clear()
            print("Current model: %s" % settings["model"])
            raw = input("New model (empty=cancel): ").strip()
            if raw:
                settings["model"] = raw
                cfg.save_settings(settings)
        elif choice == 2:
            _clear()
            print("Local [%s] / H200 [%s] / Renderer [%s]" % (
                settings["endpoint"], settings["h200Endpoint"], settings["rendererService"]))
            local = input("Local endpoint (empty=keep): ").strip()
            h200 = input("H200 endpoint (empty=keep): ").strip()
            renderer = input("Renderer (empty=keep): ").strip()
            if local:
                settings["endpoint"] = local
            if h200:
                settings["h200Endpoint"] = h200
            if renderer:
                settings["rendererService"] = renderer
            cfg.save_settings(settings)
        elif choice == 3:
            _clear()
            print("Checking H200 tunnel + remote model (honest, no silent fallback)...")
            import urllib.request

            def _probe_h200():
                endpoint = settings["h200Endpoint"].rstrip("/")
                with urllib.request.urlopen(endpoint + "/api/version", timeout=5) as r:
                    version = json.load(r)
                with urllib.request.urlopen(endpoint + "/api/tags", timeout=10) as r:
                    tags = json.load(r)
                return version, tags

            try:
                version, tags = screen_mod.run_with_spinner(
                    "Probing H200 tunnel + remote model", _probe_h200)
                print("tunnel OK: %s" % version)
                names = [m.get("name") for m in tags.get("models", [])]
                print("remote models: %s" % names[:10])
                print("wanted %s: %s" % (settings["model"],
                                         "PRESENT" if settings["model"] in names else "MISSING — do not run, fix server first"))
            except Exception as error:
                print("H200 DOWN: %s" % error)
                print("Staying on current mode; nothing silently switched.")
            _pause()


def main() -> int:
    """Crash-safe entry: Ctrl+C exits the TUI only, never the agent or evidence."""
    try:
        return _main()
    except KeyboardInterrupt:
        print("\nInterrupted; the agent (if any) keeps running and no evidence was touched.")
        return 130
    finally:
        try:
            services_mod.heartbeat_stop()
            for line in services_mod.release_lines(services_mod.release_owned_services()):
                print(line)
        except Exception as error:
            print("Service release skipped: %s" % error)


def _main() -> int:
    if not sys.stdin.isatty():
        print("Volume Agent TUI needs an interactive PowerShell console (arrow keys + Enter).")
        print("Open Windows PowerShell or Windows Terminal and run .\\Start-VolumeTUI.ps1 there.")
        return 2
    settings = cfg.load_settings()
    svc = screen_mod.run_with_spinner(
        "Starting owned background services (Local loopback only)",
        services_mod.ensure_services, settings)
    for line in services_mod.report_lines(svc):
        print(line)
    if svc.get("owns"):
        services_mod.heartbeat_start()
    env_state = screen_mod.run_with_spinner(
        "Running preflight checks", env_mod.check_environment, settings)
    if not env_state["ok"]:
        _clear()
        print("Volume Agent TUI — preflight (no model call made)")
        for item in env_state["items"]:
            _print_check(item)
        print("-" * 70)
        if _menu("Continue anyway?", ["Missing files/tools stop before any Run."], ["Enter TUI", "Exit"]) != 0:
            return 0
    while True:
        lines = _status_lines(settings)
        choice = _menu("Volume Agent TUI  (control layer; Codex CLI owns all runs)",
                       lines, ["Run Task A", "Run Task B", "Custom Task", "History",
                               "Settings", "Exit"])
        if choice is None or choice == 5:
            # Closing the TUI never terminates a running agent.
            still = [r for r in runs_mod.discover_runs(limit=50) if r["status"] == "Running"]
            _clear()
            if still:
                print("Agent %s is still running. Exiting the TUI keeps it alive; re-enter to follow via History." % still[0]["runId"])
                _pause("Enter=exit TUI (agent keeps running)")
            return 0
        if choice == 0:
            _confirm_and_run("task-a", "Task A: rotationXYZ [25,-35,10]", settings)
        elif choice == 1:
            _confirm_and_run("task-b", "Task B: autonomous visual feedback (>=2 renders)", settings)
        elif choice == 2:
            _confirm_and_run("custom", "Custom task", settings)
        elif choice == 3:
            _history()
        elif choice == 4:
            settings = _settings(settings)


if __name__ == "__main__":
    raise SystemExit(main())
