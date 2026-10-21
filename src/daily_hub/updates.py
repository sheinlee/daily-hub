"""Startup check: which tools were updated since the hub was last opened, and does the hub still fit them.

Runs in a background thread at every start (gui.MainWindow.start_update_check):
- the installed version of each tool is compared with the one seen at the previous start
  (config/hub_state.json) and with the version the hub was checked against (adapter.verified_version);
- for a tool whose version changed, the run markers (markers.py) and the daily-settings page (version,
  new / missing / retyped options) are checked;
- a report for Claude is written to logs/update-report-<time>.md, so the adapters can be brought up to
  date without a new investigation.
Nothing in the tools is changed and nothing is started.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

from . import adapters, settings
from .config import LOG_DIR, PROJECT_ROOT, HubConfig

STATE_PATH = PROJECT_ROOT / "config" / "hub_state.json"


@dataclass
class ToolReport:
    key: str
    name: str
    tool: str
    version: str | None
    last: str | None              # version seen at the previous start (None: first start)
    verified_run: str             # adapter.verified_version
    verified_settings: str        # settings provider verified_version ("" = no settings page)
    markers_missing: list[str] = field(default_factory=list)
    settings_drift: list[str] = field(default_factory=list)
    settings_error: str = ""
    checked: bool = False         # deep checks ran (version changed, first start, or forced)
    first: bool = False           # no record from a previous start

    @property
    def changed(self) -> bool:
        return not self.first and _norm(self.version) != _norm(self.last)

    @property
    def needs_attention(self) -> bool:
        return self.changed or bool(self.markers_missing or self.settings_error)


def _norm(v) -> str:
    return str(v or "").strip().lstrip("vV")


def load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_versions(reports: list[ToolReport]) -> None:
    state = load_state()
    state["versions"] = {r.key: r.version for r in reports}
    state["checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(STATE_PATH) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


def check(cfg: HubConfig, force: bool = False) -> list[ToolReport]:
    """One report per game.  Deep checks only where the version changed (or on force / first start)."""
    seen = load_state().get("versions") or {}
    out = []
    for g in cfg.ordered():
        ad = adapters.get(g.adapter)
        prov = settings.get(g.adapter)
        try:
            version = ad.version(g)
        except Exception:          # a broken version file must not stop the hub from starting
            version = None
        r = ToolReport(g.key, g.name, ad.tool_name, version, seen.get(g.key), ad.verified_version,
                       prov.verified_version if prov else "", first=g.key not in seen)
        if force or r.first or r.changed:
            r.checked = True
            try:
                r.markers_missing = ad.marker_problems(g)
            except Exception as e:
                r.markers_missing = [f"检查出错：{e!r}"]
            if prov:
                try:
                    model = prov.open(g, prov.default_config(g))
                    r.settings_drift = list(model.drift) + [f"{os.path.basename(f.path)} 只能查看：{f.reason}"
                                                            for f in model.files if not f.exact]
                except Exception as e:
                    r.settings_error = str(e) or repr(e)
        out.append(r)
    return out


def summary(reports: list[ToolReport]) -> str:
    """Short text for the startup dialog."""
    lines = []
    for r in reports:
        if not r.needs_attention:
            continue
        head = f"{r.name}（{r.tool}）"
        if r.changed:
            head += f"：{r.last} → {r.version}"
        lines.append(head)
        if r.changed and r.verified_run and _norm(r.version) != _norm(r.verified_run):
            lines.append(f"  · 运行规则按 {r.verified_run} 核对过，第一次跑这款时留意结果")
        for m in r.markers_missing:
            lines.append(f"  · {m}")
        if r.settings_error:
            lines.append(f"  · 日常设置页读不了：{r.settings_error}")
        elif r.settings_drift:
            lines.append(f"  · 日常设置页：{len(r.settings_drift)} 处变化（新选项只读显示，类型变化的已锁定）")
        elif r.checked and r.verified_settings:
            lines.append("  · 日常设置页：没有发现变化")
        if r.checked and not r.markers_missing:
            lines.append("  · 运行判定用的文字都还在")
    return "\n".join(lines)


def write_report(reports: list[ToolReport]) -> str:
    """Markdown report for Claude (versions, missing markers, settings drift).  Returns its path."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"update-report-{time.strftime('%Y%m%d-%H%M%S')}.md"
    out = ["# 脚本更新检查", "", f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}", "",
           "把这份报告交给 Claude，用来更新 daily-hub 的适配（adapters/、settings/）。", ""]
    for r in reports:
        out.append(f"## {r.name} · {r.tool}")
        last = "（第一次记录）" if r.first else (r.last or "读不到")
        out.append(f"- 版本：现在 {r.version or '读不到'}；上次打开时 {last}；"
                   f"运行规则核对版本 {r.verified_run or '—'}；日常设置核对版本 {r.verified_settings or '—'}")
        if not r.checked:
            out.append("- 版本没变，没有做深入检查")
        else:
            out.append("- 运行判定文字：" + ("全部找到" if not r.markers_missing else "；".join(r.markers_missing)))
            if r.settings_error:
                out.append(f"- 日常设置页读取失败：{r.settings_error}")
            elif r.verified_settings:
                out.append("- 日常设置页变化：" + ("无" if not r.settings_drift else ""))
                out += [f"  - {d}" for d in r.settings_drift]
        out.append("")
    path.write_text("\n".join(out), encoding="utf-8")
    return str(path)
