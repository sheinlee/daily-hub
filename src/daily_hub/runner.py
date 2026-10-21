"""Serial scheduler: for each enabled game, start game (if needed) -> run tool -> clean up -> next."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import psutil

from . import adapters, games, procs, winapi
from .adapters.base import ABORT, DONE, FAIL, KEY, WARN
from .config import LAUNCH_BY_HUB, LAUNCH_BY_TOOL, LAUNCH_VIA_LAUNCHER, LOG_DIR, GameConfig, HubConfig

PENDING = "待运行"
RUNNING = "运行中"
DONE_S = "已完成"
ERROR = "异常/跳过"
STOPPED = "已停止"

DONE_GRACE_EXITING = 120   # seconds a self-exiting tool gets after its "done" line
DONE_GRACE_RESIDENT = 10   # seconds before the hub closes a tool that stays open (BetterGI)
TOOL_CLOSE_WAIT = 15
MINIMIZED_LIMIT = 120      # seconds the game may stay minimized/hung before the item is aborted
FAIL_GRACE = 20            # after a failure line, give the tool this long to exit by itself
GAME_GONE_GRACE = 60       # the game vanished (crash / closed): wait this long before giving up


@dataclass
class Events:
    status: Callable[[str, str, str], None] = lambda key, status, msg: None
    log: Callable[[str], None] = lambda text: None
    phase: Callable[[str, str, float], None] = lambda key, phase, t0: None
    finished: Callable[[], None] = lambda: None


@dataclass
class Outcome:
    status: str
    message: str
    abort_queue: bool = False


@dataclass
class _Watch:
    done: str | None = None
    done_at: float | None = None
    fail: str | None = None
    fail_at: float | None = None
    abort: bool = False
    warns: list[str] = field(default_factory=list)
    last_key: str | None = None


class Runner:
    def __init__(self, cfg: HubConfig, events: Events):
        self.cfg = cfg
        self.ev = events
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._logfile = None

    # ---- public ----
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, keys: list[str]) -> bool:
        if self.running:
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_queue, args=(keys,), daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopped_by_user(self) -> bool:
        """After a queue: was it ended with stop() (the after-run power action is skipped then)?"""
        return self._stop.is_set()

    # ---- logging ----
    def log(self, text: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {text}"
        self.ev.log(line)
        if self._logfile:
            try:
                self._logfile.write(line + "\n")
                self._logfile.flush()
            except OSError:
                pass

    # ---- queue ----
    def _run_queue(self, keys: list[str]) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / f"hub-{time.strftime('%Y%m%d-%H%M%S')}.log"
        try:
            self._logfile = open(path, "a", encoding="utf-8")
        except OSError:
            self._logfile = None
        try:
            items = [self.cfg.games[k] for k in keys if k in self.cfg.games]
            for g in items:
                self.ev.status(g.key, PENDING, "")
            self.log(f"===== 开始：{' → '.join(g.name for g in items)}（日志 {path.name}）")
            for idx, g in enumerate(items):
                if self._stop.is_set():
                    for rest in items[idx:]:
                        self.ev.status(rest.key, PENDING, "队列已停止，未运行")
                    break
                self.log(f"----- [{idx + 1}/{len(items)}] {g.name}（{adapters.get(g.adapter).tool_name}）")
                try:
                    out = self._run_one(g)
                except Exception as e:  # never let one game kill the queue thread silently
                    out = Outcome(ERROR, f"总控内部错误：{e!r}", abort_queue=True)
                self.ev.status(g.key, out.status, out.message)
                self.log(f"{g.name}：{out.status} — {out.message}")
                if out.status == STOPPED:
                    for rest in items[idx + 1:]:
                        self.ev.status(rest.key, PENDING, "队列已停止，未运行")
                    break
                if out.abort_queue or (out.status == ERROR and not self.cfg.continue_on_error):
                    for rest in items[idx + 1:]:
                        self.ev.status(rest.key, PENDING, "前一款异常，队列中止")
                    self.log("队列中止")
                    break
            self.log("===== 结束")
        finally:
            if self._logfile:
                self._logfile.close()
                self._logfile = None
            self.ev.finished()

    # ---- one game ----
    def _phase(self, g: GameConfig, text: str, t0: float) -> None:
        self.ev.phase(g.key, text, t0)

    def _run_one(self, g: GameConfig) -> Outcome:
        ad = adapters.get(g.adapter)
        t0 = time.time()
        self.ev.status(g.key, RUNNING, "预检")
        self._phase(g, "预检", t0)

        problems = []
        if not winapi.is_admin():
            problems.append("总控未以管理员身份运行（游戏与工具都需要管理员权限）")
        problems += ad.problems(g)
        mode = ad.game_mode(g)
        if mode == LAUNCH_BY_HUB and not g.game_exe:
            problems.append("未配置游戏程序路径")
        if mode == LAUNCH_VIA_LAUNCHER and not g.launcher_exe:
            problems.append("未配置启动器路径")
        if problems:
            return Outcome(ERROR, "；".join(problems))
        inst = ad.running_instances(g)
        if inst:
            return Outcome(ERROR, "；".join(inst) + "。请先关闭它再运行（总控不会替你关掉手动打开的工具）")
        for w in ad.warnings(g):
            self.log(f"  【注意】{w}")

        # -- game --
        if mode != LAUNCH_BY_TOOL:
            started_now = not games.is_running(g)
            if not started_now:
                self.log("游戏已在运行，直接使用")
            else:
                self._phase(g, "启动游戏", t0)
                try:
                    if mode == LAUNCH_VIA_LAUNCHER:
                        games.launch_via_launcher(g, self.log, self._stop.is_set, ad.launcher_probe(g))
                    else:
                        games.launch(g, self.log)
                except RuntimeError as e:
                    return self._finish_game(g, Outcome(ERROR, str(e)))
                if self._stop.is_set():
                    return Outcome(STOPPED, "启动游戏阶段被停止（游戏保持打开）")
            self._phase(g, "等待游戏窗口", t0)
            w = games.wait_ready(g, g.game_ready_timeout_s, self._stop.is_set, self.log)
            if self._stop.is_set():
                return Outcome(STOPPED, "启动游戏阶段被停止（游戏保持打开）")
            if w is None:
                return self._finish_game(g, Outcome(ERROR, f"{g.game_ready_timeout_s} 秒内没有等到可用的游戏窗口"))
            delay = g.game_ready_delay_s if started_now else min(g.game_ready_delay_s, 5)
            self.log(f"游戏窗口就绪 {w.width}x{w.height}，再等 {delay} 秒")
            if self._sleep(delay):
                return Outcome(STOPPED, "等待游戏就绪时被停止（游戏保持打开）")
            # The client may have restarted itself meanwhile (鸣潮 does ~2 min after launch): check again.
            w = games.wait_ready(g, 120, self._stop.is_set, lambda _m: None)
            if self._stop.is_set():
                return Outcome(STOPPED, "启动游戏阶段被停止（游戏保持打开）")
            if w is None:
                return self._finish_game(g, Outcome(ERROR, "游戏窗口在就绪等待后消失且没有恢复"))
            games.focus(g)
        elif games.is_running(g):
            self.log("游戏已在运行，确保窗口未最小化")
            if games.ensure_visible(g) == games.HUNG:
                self.log("警告：游戏窗口当前无响应")
            games.focus(g)

        # -- tool --
        spec = ad.launch_spec(g)
        self._phase(g, "运行脚本", t0)
        self.log(f"启动 {ad.tool_name}：{' '.join(spec.argv)}")
        since = time.time()
        try:
            popen = procs.launch(spec.argv, spec.cwd, spec.env, spec.console, spec.capture)
        except OSError as e:
            return self._finish_game(g, Outcome(ERROR, f"启动 {ad.tool_name} 失败：{e}"))
        tracked = procs.Tracked(popen, exclude_names={n.lower() for n in g.game_processes})
        watch = _Watch()
        session = None
        try:
            session = adapters.base.Session(ad, g, popen, since)
            reason, detail = self._monitor(g, ad, popen, tracked, session, watch)
        except Exception as e:  # whatever happens, fall through to the cleanup below
            reason, detail = "error", repr(e)

        # -- make sure the tool is gone (always runs once the tool was started) --
        self._phase(g, "收尾", t0)
        ended_by_hub = popen.poll() is None
        survivors = self._stop_tool(g, ad, tracked, graceful=reason not in ("stop", "error", "abort"))
        if session is not None:
            try:
                self._consume(session.poll(final=True), watch)
            except Exception:
                pass
        rc = "已由总控结束" if ended_by_hub else f"退出码 {popen.poll()}"

        if survivors:
            return Outcome(ERROR, f"脚本进程无法结束：{'、'.join(survivors)}，为安全起见中止队列", abort_queue=True)
        if reason == "stop":
            return Outcome(STOPPED, f"已停止 {ad.tool_name}（游戏保持打开）")

        if reason == "error":
            out = Outcome(ERROR, f"总控内部错误：{detail}；已结束 {ad.tool_name}", abort_queue=True)
        elif reason == "abort":
            out = Outcome(ERROR, f"已立即结束 {ad.tool_name}：{_short(watch.fail)}")
        elif reason == "timeout":
            out = Outcome(ERROR, f"超时（{g.timeout_min} 分钟），已结束 {ad.tool_name}")
        elif reason == "window":
            out = Outcome(ERROR, f"{detail}超过 {MINIMIZED_LIMIT} 秒，已结束 {ad.tool_name}")
        elif reason == "game_gone":
            out = Outcome(ERROR, f"游戏进程中途退出，已结束 {ad.tool_name}")
        elif watch.fail:
            out = Outcome(ERROR, f"失败：{_short(watch.fail)}（{rc}）")
        elif watch.done:
            extra = f"，{len(watch.warns)} 条警告（最后一条：{_short(watch.warns[-1])}）" if watch.warns else ""
            out = Outcome(DONE_S, f"{_short(watch.done)}{extra}（{rc}）")
        else:
            last = f"；最后关键行：{_short(watch.last_key)}" if watch.last_key else ""
            out = Outcome(ERROR, f"未看到完成标记（{rc}）{last}")
        return self._finish_game(g, out)

    def _monitor(self, g, ad, popen, tracked, session, watch) -> tuple[str | None, str]:
        """Poll until the tool finishes, fails, times out or is stopped. Returns (reason, detail);
        reason None means the tool ended on its own terms (exit, done/fail line)."""
        deadline = time.monotonic() + g.timeout_min * 60
        bad_window_since: float | None = None
        game_seen = games.is_running(g)
        game_gone_since: float | None = None
        last_check = 0.0
        while True:
            self._consume(session.poll(), watch)
            tracked.refresh()
            if self._stop.is_set():
                return "stop", ""
            if popen.poll() is not None:
                return None, ""
            now = time.monotonic()
            if now >= deadline:
                return "timeout", ""
            if watch.abort:
                self.log(f"{ad.tool_name} 报告了必须立即停止的情况，立即结束")
                return "abort", ""
            if watch.fail_at is not None and now - watch.fail_at >= FAIL_GRACE:
                self.log(f"{ad.tool_name} 已报告失败，{FAIL_GRACE} 秒内未退出，由总控结束")
                return None, ""
            if watch.done_at is not None:
                grace = DONE_GRACE_EXITING if ad.exits_when_done else DONE_GRACE_RESIDENT
                if now - watch.done_at >= grace:
                    self.log(f"已看到完成标记，{ad.tool_name} {'未自行退出' if ad.exits_when_done else '常驻'}，由总控关闭")
                    return None, ""
            if now - last_check >= 5:
                last_check = now
                # Game still there?  A tool may restart the game, so only give up after a grace period.
                if games.is_running(g):
                    game_seen, game_gone_since = True, None
                elif game_seen:
                    game_gone_since = game_gone_since or now
                    if now - game_gone_since >= getattr(ad, "game_gone_grace", GAME_GONE_GRACE):
                        return "game_gone", ""
                # Window minimized / not responding?
                prob = games.ensure_visible(g)
                if prob:
                    if bad_window_since is None:
                        self.log("游戏窗口被最小化，已请求恢复" if prob == games.MINIMIZED else "游戏窗口无响应")
                    bad_window_since = bad_window_since or now
                    if now - bad_window_since >= MINIMIZED_LIMIT:
                        return "window", ("游戏窗口持续最小化" if prob == games.MINIMIZED else "游戏窗口无响应")
                else:
                    bad_window_since = None
            time.sleep(1)

    def _finish_game(self, g: GameConfig, out: Outcome) -> Outcome:
        if not g.close_game_after:
            self.log("按设置保留游戏客户端")
            return out
        if not games.is_running(g):
            return out
        self._phase(g, "关闭游戏", time.time())
        if not games.close(g, g.close_timeout_s, self.log):
            out.message += "；游戏进程未能全部关闭"
        return out

    def _stop_tool(self, g: GameConfig, ad, tracked: procs.Tracked, graceful: bool) -> list[str]:
        tracked.refresh()
        if tracked.root_running() and graceful and not ad.exits_when_done:
            n = tracked.close_windows()
            if n:
                self.log(f"向 {ad.tool_name} 发送关闭请求（{n} 个窗口）")
                end = time.monotonic() + TOOL_CLOSE_WAIT
                while time.monotonic() < end and tracked.root_running():
                    time.sleep(1)
        names = "、".join(n for n in (_name_pid(p) for p in tracked.alive()) if n)
        if names:
            self.log(f"结束脚本进程：{names}")
        survivors = tracked.kill_all()
        # Helpers (e.g. MaaEnd's go-service/cpp-algo) that escaped the tree: the precheck made sure
        # none were running from this install before we started, so anything left there is ours.
        extra = ad.extra_cleanup_names()
        if extra:
            for i in procs.under_dir(g.tool_dir, extra):
                try:
                    psutil.Process(i["pid"]).kill()
                    self.log(f"结束残留辅助进程：{i['name']}({i['pid']})")
                except psutil.Error:
                    pass
            time.sleep(1)
            survivors += [f'{i["name"]}({i["pid"]})' for i in procs.under_dir(g.tool_dir, extra)]
        return survivors

    def _consume(self, lines: list[tuple[str, str | None]], w: _Watch) -> None:
        for text, kind in lines:
            if kind == DONE and w.done is None:
                w.done, w.done_at = text, time.monotonic()
            elif kind in (FAIL, ABORT):
                w.fail = text
                w.fail_at = w.fail_at or time.monotonic()
                w.abort = w.abort or kind == ABORT
            elif kind == WARN:
                w.warns.append(text)
            if kind in (KEY, DONE, FAIL, ABORT):
                w.last_key = text
            tag = {DONE: "【完成】", FAIL: "【失败】", ABORT: "【失败】", WARN: "【警告】"}.get(kind, "")
            self.log(f"  {tag}{text}")

    def _sleep(self, seconds: float) -> bool:
        """Interruptible sleep. Returns True if stop was requested."""
        return self._stop.wait(max(0.0, seconds))


def _short(s: str | None, n: int = 120) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _name_pid(p) -> str:
    try:
        return f"{p.name()}({p.pid})"
    except Exception:
        return ""
