#!/usr/bin/env python3
"""Automatically redistribute Niri column widths.

This daemon listens to Niri's JSON event stream, coalesces relevant layout
changes, and resizes the tiled columns on every active workspace. It uses only
the Python standard library and is designed to be supervised by the Noctalia
v5 service entry, but it can also be run standalone.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass(slots=True)
class RuntimeConfig:
    enabled: bool = True
    max_visible: int = 4
    only_at_max: bool = True
    center_single_window: bool = True
    per_workspace: bool = False
    workspace_max_visible: dict[str, int] = field(default_factory=dict)
    debounce_ms: int = 300
    max_events_per_second: int = 20
    debug: bool = False


class NiriCommandError(RuntimeError):
    pass


def clamp_int(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = fallback
    return max(minimum, min(maximum, number))


def as_bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return fallback


class AutoTileDaemon:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.config_file = Path(args.config_file).expanduser() if args.config_file else None
        self.pid_file = Path(args.pid_file).expanduser() if args.pid_file else None
        self.config = RuntimeConfig(
            enabled=True,
            max_visible=clamp_int(args.max_visible, 4, 1, 8),
            only_at_max=bool(args.only_at_max),
            center_single_window=bool(args.center_single_window),
            per_workspace=bool(args.per_workspace),
            workspace_max_visible=self._parse_workspace_json(args.workspace_config),
            debounce_ms=clamp_int(round(args.debounce * 1000), 300, 50, 2000),
            max_events_per_second=clamp_int(args.max_events, 20, 1, 100),
            debug=bool(args.debug),
        )

        self.stop_event = threading.Event()
        self.redistribute_lock = threading.Lock()
        self.timer_lock = threading.Lock()
        self.config_lock = threading.Lock()
        self.event_process: subprocess.Popen[str] | None = None
        self.debounce_timer: threading.Timer | None = None
        self.event_times: deque[float] = deque()
        self.known_window_ids: set[int] = set()
        self.last_layout_signature: tuple[Any, ...] | None = None
        self.last_applied: dict[int, tuple[Any, ...]] = {}
        self.config_generation = 0
        self.rate_limited_until = 0.0

        self.reload_config(initial=True)

    @staticmethod
    def _parse_workspace_json(raw: Any) -> dict[str, int]:
        if raw is None or raw == "":
            return {}
        try:
            decoded = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            return {}
        if not isinstance(decoded, dict):
            return {}
        result: dict[str, int] = {}
        for key, value in decoded.items():
            result[str(key)] = clamp_int(value, 4, 1, 8)
        return result

    def log(self, level: str, message: str) -> None:
        if level == "DEBUG" and not self.config.debug:
            return
        print(f"[{level}] {message}", flush=True)

    def status(self, message: str, **extra: Any) -> None:
        payload: dict[str, Any] = {"message": message}
        payload.update(extra)
        print("STATUS " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)

    def load_file_config(self) -> dict[str, Any]:
        if self.config_file is None or not self.config_file.exists():
            return {}
        try:
            data = json.loads(self.config_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.log("ERROR", f"Could not read runtime config: {exc}")
            return {}
        if not isinstance(data, dict):
            self.log("ERROR", "Runtime config must contain a JSON object")
            return {}
        return data

    def reload_config(self, *, initial: bool = False) -> None:
        data = self.load_file_config()
        with self.config_lock:
            current = self.config
            workspace_raw = data.get(
                "workspaceMaxVisible",
                data.get("workspace_max_visible", current.workspace_max_visible),
            )
            workspace_values = self._parse_workspace_json(workspace_raw)
            self.config = RuntimeConfig(
                enabled=as_bool(data.get("enabled"), current.enabled),
                max_visible=clamp_int(
                    data.get("maxVisible", data.get("max_visible")),
                    current.max_visible,
                    1,
                    8,
                ),
                only_at_max=as_bool(
                    data.get("onlyAtMax", data.get("only_at_max")),
                    current.only_at_max,
                ),
                center_single_window=as_bool(
                    data.get(
                        "centerSingleWindow",
                        data.get("center_single_window"),
                    ),
                    current.center_single_window,
                ),
                per_workspace=as_bool(
                    data.get("perWorkspace", data.get("per_workspace")),
                    current.per_workspace,
                ),
                workspace_max_visible=workspace_values,
                debounce_ms=clamp_int(
                    data.get("debounceMs", data.get("debounce_ms")),
                    current.debounce_ms,
                    50,
                    2000,
                ),
                max_events_per_second=clamp_int(
                    data.get("maxEventsPerSecond", data.get("max_events_per_second")),
                    current.max_events_per_second,
                    1,
                    100,
                ),
                debug=as_bool(data.get("debug"), current.debug),
            )
            self.config_generation += 1
            self.last_applied.clear()

        if not initial:
            self.log("INFO", "Configuration reloaded")
            self.status(
                "configuration reloaded",
                enabled=self.config.enabled,
                maxVisible=self.config.max_visible,
            )

    def acquire_pid_file(self) -> None:
        if self.pid_file is None:
            return
        try:
            if self.pid_file.exists():
                raw = self.pid_file.read_text(encoding="utf-8").strip()
                old_pid = int(raw)
                if old_pid != os.getpid():
                    try:
                        os.kill(old_pid, 0)
                    except ProcessLookupError:
                        pass
                    except PermissionError as exc:
                        raise RuntimeError(
                            f"Another daemon may already own PID {old_pid}"
                        ) from exc
                    else:
                        raise RuntimeError(f"Daemon already running with PID {old_pid}")
            self.pid_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.pid_file.with_suffix(self.pid_file.suffix + ".tmp")
            temporary.write_text(str(os.getpid()) + "\n", encoding="utf-8")
            temporary.replace(self.pid_file)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Could not create PID file: {exc}") from exc

    def remove_pid_file(self) -> None:
        if self.pid_file is None:
            return
        try:
            if self.pid_file.exists():
                raw = self.pid_file.read_text(encoding="utf-8").strip()
                if not raw or int(raw) == os.getpid():
                    self.pid_file.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass

    def install_signal_handlers(self) -> None:
        def request_stop(signum: int, _frame: Any) -> None:
            self.log("INFO", f"Received signal {signum}; stopping")
            self.stop_event.set()
            with self.timer_lock:
                if self.debounce_timer is not None:
                    self.debounce_timer.cancel()
            process = self.event_process
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    process.terminate()

        def request_reload(_signum: int, _frame: Any) -> None:
            self.reload_config()
            self.schedule_redistribution("config reload")

        def request_redistribution(_signum: int, _frame: Any) -> None:
            self.schedule_redistribution("manual request", force=True, immediate=True)

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGUSR1, request_reload)
        signal.signal(signal.SIGUSR2, request_redistribution)

    def run_json(self, request: str) -> Any:
        try:
            completed = subprocess.run(
                ["niri", "msg", "--json", request],
                check=False,
                capture_output=True,
                text=True,
                timeout=4,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NiriCommandError(f"niri msg {request} failed: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
            raise NiriCommandError(f"niri msg {request}: {detail}")
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise NiriCommandError(f"Invalid JSON from niri msg {request}") from exc

    def run_action(self, action: str, *arguments: str) -> bool:
        command = ["niri", "msg", "action", action, *arguments]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=4,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.log("ERROR", f"Action {action} failed: {exc}")
            return False
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
            self.log("ERROR", f"Action {action} failed: {detail}")
            return False
        return True

    def allow_event(self) -> bool:
        now = time.monotonic()
        with self.config_lock:
            maximum = self.config.max_events_per_second
        while self.event_times and now - self.event_times[0] > 1.0:
            self.event_times.popleft()
        if len(self.event_times) >= maximum:
            if now >= self.rate_limited_until:
                self.rate_limited_until = now + 1.0
                self.log("WARNING", "Event rate limit reached; coalescing layout updates")
            return False
        self.event_times.append(now)
        return True

    def schedule_redistribution(
        self,
        reason: str,
        *,
        force: bool = False,
        immediate: bool = False,
    ) -> None:
        with self.config_lock:
            enabled = self.config.enabled
            delay = 0.01 if immediate else self.config.debounce_ms / 1000.0
        if not enabled and not force:
            return
        if not force and not self.allow_event():
            return

        with self.timer_lock:
            if self.debounce_timer is not None:
                self.debounce_timer.cancel()
            self.debounce_timer = threading.Timer(
                delay,
                self.redistribute_safely,
                kwargs={"reason": reason, "force": force},
            )
            self.debounce_timer.daemon = True
            self.debounce_timer.start()
        self.log("DEBUG", f"Scheduled redistribution: {reason}")

    @staticmethod
    def tiled_position(window: dict[str, Any]) -> tuple[int, int] | None:
        if window.get("is_floating") is True:
            return None
        layout = window.get("layout")
        if not isinstance(layout, dict):
            return None
        position = layout.get("pos_in_scrolling_layout")
        if not isinstance(position, (list, tuple)) or len(position) < 2:
            return None
        try:
            return int(position[0]), int(position[1])
        except (TypeError, ValueError):
            return None

    @staticmethod
    def workspace_key_candidates(workspace: dict[str, Any]) -> Iterable[str]:
        for key in ("id", "idx", "index", "name"):
            value = workspace.get(key)
            if value is not None and str(value) != "":
                yield str(value)

    def limit_for_workspace(self, workspace: dict[str, Any]) -> int:
        with self.config_lock:
            config = self.config
            default = config.max_visible
            if not config.per_workspace:
                return default
            for candidate in self.workspace_key_candidates(workspace):
                if candidate in config.workspace_max_visible:
                    return clamp_int(config.workspace_max_visible[candidate], default, 1, 8)
            return default

    def redistribute_safely(self, *, reason: str, force: bool) -> None:
        if self.stop_event.is_set():
            return
        if not self.redistribute_lock.acquire(blocking=False):
            self.log("DEBUG", "A redistribution is already running")
            return
        try:
            self.redistribute(reason=reason, force=force)
        except Exception as exc:  # Keep the long-lived daemon alive on unexpected data.
            self.log("ERROR", f"Redistribution failed: {exc}")
            self.status("redistribution failed", error=str(exc))
        finally:
            self.redistribute_lock.release()

    def redistribute(self, *, reason: str, force: bool) -> None:
        with self.config_lock:
            config = self.config
            generation = self.config_generation
        if not config.enabled and not force:
            return

        workspaces = self.run_json("workspaces")
        windows = self.run_json("windows")
        focused = self.run_json("focused-window")
        if not isinstance(workspaces, list) or not isinstance(windows, list):
            raise NiriCommandError("Unexpected workspaces/windows response")

        active_workspaces = [ws for ws in workspaces if ws.get("is_active") is True]
        if not active_workspaces:
            active_workspaces = [ws for ws in workspaces if ws.get("is_focused") is True]

        focused_id = focused.get("id") if isinstance(focused, dict) else None
        changed = 0
        details: list[dict[str, Any]] = []

        for workspace in active_workspaces:
            workspace_id = workspace.get("id")
            if workspace_id is None:
                continue
            try:
                numeric_workspace_id = int(workspace_id)
            except (TypeError, ValueError):
                continue

            positioned: list[tuple[int, int, dict[str, Any]]] = []
            for window in windows:
                if window.get("workspace_id") != workspace_id:
                    continue
                position = self.tiled_position(window)
                if position is not None:
                    positioned.append((position[0], position[1], window))
            if not positioned:
                continue

            positioned.sort(key=lambda item: (item[0], item[1]))
            column_indices = sorted({item[0] for item in positioned})
            column_count = len(column_indices)
            tiled_window_count = len(positioned)
            maximum = self.limit_for_workspace(workspace)

            # Centering is intentionally handled before only_at_max. This lets a
            # lone tiled window be centered without changing its width, even when
            # automatic redistribution is waiting for the configured limit.
            if config.center_single_window and tiled_window_count == 1:
                single_window_id = positioned[0][2].get("id")
                if single_window_id is None:
                    continue
                center_cache = (
                    "center-single",
                    int(single_window_id),
                    generation,
                )
                if not force and self.last_applied.get(numeric_workspace_id) == center_cache:
                    continue
                if not self.run_action("focus-window", "--id", str(single_window_id)):
                    continue
                if self.run_action("center-column"):
                    self.last_applied[numeric_workspace_id] = center_cache
                    changed += 1
                    details.append(
                        {
                            "workspace": workspace_id,
                            "windows": tiled_window_count,
                            "columns": column_count,
                            "mode": "center-single-window",
                        }
                    )
                continue

            if config.only_at_max and column_count < maximum and not force:
                self.log(
                    "DEBUG",
                    f"Workspace {workspace_id}: {column_count}/{maximum} columns; below limit",
                )
                continue

            visible_columns = min(column_count, maximum)
            cache_value = ("redistribute", column_count, maximum, generation)
            if not force and self.last_applied.get(numeric_workspace_id) == cache_value:
                continue

            first_window_id = positioned[0][2].get("id")
            if first_window_id is None:
                continue
            if not self.run_action("focus-window", "--id", str(first_window_id)):
                continue
            if not self.run_action("focus-column-first"):
                continue

            width = 100.0 / max(1, visible_columns)
            width_argument = f"{width:.8f}".rstrip("0").rstrip(".") + "%"
            successful = True
            for index in range(column_count):
                if not self.run_action("set-column-width", width_argument):
                    successful = False
                    break
                if index + 1 < column_count and not self.run_action("focus-column-right"):
                    successful = False
                    break

            if successful:
                self.run_action("center-visible-columns")
                self.last_applied[numeric_workspace_id] = cache_value
                changed += 1
                details.append(
                    {
                        "workspace": workspace_id,
                        "columns": column_count,
                        "limit": maximum,
                        "width": width_argument,
                    }
                )

        if focused_id is not None:
            self.run_action("focus-window", "--id", str(focused_id))

        self.log("DEBUG", f"Redistribution complete ({reason}); changed {changed} workspace(s)")
        self.status(
            "redistribution complete",
            reason=reason,
            changedWorkspaces=changed,
            workspaces=details,
        )

    @staticmethod
    def event_window_ids(payload: Any) -> set[int]:
        if not isinstance(payload, dict):
            return set()
        windows = payload.get("windows")
        if not isinstance(windows, list):
            return set()
        result: set[int] = set()
        for window in windows:
            if isinstance(window, dict) and isinstance(window.get("id"), int):
                result.add(window["id"])
        return result

    @staticmethod
    def event_layout_signature(payload: Any) -> tuple[Any, ...] | None:
        if not isinstance(payload, dict):
            return None
        windows = payload.get("windows")
        if not isinstance(windows, list):
            return None
        signature: list[tuple[Any, ...]] = []
        for window in windows:
            if not isinstance(window, dict):
                continue
            layout = window.get("layout") if isinstance(window.get("layout"), dict) else {}
            position = layout.get("pos_in_scrolling_layout")
            normalized_position = tuple(position) if isinstance(position, list) else None
            signature.append(
                (
                    window.get("id"),
                    window.get("workspace_id"),
                    window.get("is_floating"),
                    normalized_position,
                )
            )
        return tuple(sorted(signature, key=lambda item: str(item[0])))

    def handle_event(self, event: Any) -> None:
        if not isinstance(event, dict) or not event:
            return

        event_name, payload = next(iter(event.items()))
        if event_name == "WindowsChanged":
            current_ids = self.event_window_ids(payload)
            signature = self.event_layout_signature(payload)
            changed = (
                current_ids != self.known_window_ids
                or signature != self.last_layout_signature
            )
            self.known_window_ids = current_ids
            self.last_layout_signature = signature
            if changed:
                self.schedule_redistribution("windows changed")
            return

        if event_name == "WindowOpenedOrChanged":
            window = payload.get("window") if isinstance(payload, dict) else None
            window_id = window.get("id") if isinstance(window, dict) else None
            if isinstance(window_id, int) and window_id not in self.known_window_ids:
                self.known_window_ids.add(window_id)
                self.schedule_redistribution("window opened")
            return

        if event_name == "WindowClosed":
            window_id = payload.get("id") if isinstance(payload, dict) else None
            if isinstance(window_id, int):
                self.known_window_ids.discard(window_id)
            self.schedule_redistribution("window closed")
            return

        if event_name in {
            "WindowLayoutsChanged",
            "WorkspaceActivated",
            "WorkspacesChanged",
        }:
            self.schedule_redistribution(event_name)

    def event_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.log("DEBUG", "Connecting to niri event stream")
                self.event_process = subprocess.Popen(
                    ["niri", "msg", "--json", "event-stream"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    start_new_session=True,
                )
            except OSError as exc:
                self.log("ERROR", f"Could not start niri event stream: {exc}")
                self.status("event stream unavailable", error=str(exc))
                if self.stop_event.wait(2.0):
                    break
                continue

            process = self.event_process
            assert process.stdout is not None
            try:
                for line in process.stdout:
                    if self.stop_event.is_set():
                        break
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        event = json.loads(stripped)
                    except json.JSONDecodeError:
                        self.log("WARNING", "Ignored malformed event-stream line")
                        continue
                    self.handle_event(event)
            finally:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except (ProcessLookupError, PermissionError):
                        process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except (ProcessLookupError, PermissionError):
                            process.kill()
                stderr = ""
                if process.stderr is not None:
                    try:
                        stderr = process.stderr.read().strip()
                    except OSError:
                        stderr = ""
                self.event_process = None

            if self.stop_event.is_set():
                break
            self.log("WARNING", f"Niri event stream ended{': ' + stderr if stderr else ''}; reconnecting")
            self.status("event stream reconnecting", error=stderr)
            self.stop_event.wait(2.0)

    def run(self) -> int:
        self.acquire_pid_file()
        self.install_signal_handlers()
        print("READY", flush=True)
        self.status(
            "daemon ready",
            enabled=self.config.enabled,
            maxVisible=self.config.max_visible,
        )
        self.schedule_redistribution("startup")
        try:
            self.event_loop()
        finally:
            with self.timer_lock:
                if self.debounce_timer is not None:
                    self.debounce_timer.cancel()
            self.remove_pid_file()
            self.status("daemon stopped")
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-visible", type=int, default=4)
    parser.add_argument("--debounce", type=float, default=0.3, help="seconds")
    parser.add_argument("--max-events", type=int, default=20)
    parser.add_argument("--only-at-max", action="store_true")
    parser.add_argument(
        "--center-single-window",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--per-workspace", action="store_true")
    parser.add_argument("--workspace-config", default="{}")
    parser.add_argument("--config-file")
    parser.add_argument("--pid-file")
    parser.add_argument("--debug", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        daemon = AutoTileDaemon(args)
        return daemon.run()
    except RuntimeError as exc:
        print(f"ERROR {exc}", flush=True)
        return 2
    except Exception as exc:
        print(f"ERROR Unexpected daemon failure: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
