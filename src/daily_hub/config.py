"""Hub settings: one JSON file with paths, launch options, timeouts, enable flags and order."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Source checkout: the project folder.  Packaged build (PyInstaller, DailyHub.exe): the exe's folder, so
# config/, logs/ and backups/ sit next to the program.
FROZEN = bool(getattr(sys, "frozen", False))
PROJECT_ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "hub_config.json"
LOG_DIR = PROJECT_ROOT / "logs"
# A copy of the project in another folder (e.g. daily-hub-dev) is a separate hub: own window title,
# own single-instance pipe, own scheduled task.
EDITION = "" if FROZEN or PROJECT_ROOT.name in ("daily-hub", "daily-hub") else PROJECT_ROOT.name

_HOME = os.path.expanduser("~")
_LOCAL = os.environ.get("LOCALAPPDATA", os.path.join(_HOME, "AppData", "Local"))
# Defaults only cover standard install locations; tools that are simply unzipped somewhere start with an
# empty folder, which the precheck asks the user to fill in (总控设置 → 工具目录).

# Example tool folders shown (greyed) in empty path boxes and in the precheck hint: (example, file inside it).
TOOL_DIR_EXAMPLES = {
    "genshin": (r"C:\Program Files\BetterGI\BetterGI", "BetterGI.exe"),
    "zzz": (r"D:\Games\ZenlessZoneZero-OneDragon", "src 和 .venv 文件夹"),
    "endfield": (r"D:\Games\MaaEnd", "MaaEnd.exe"),
    "wuwa": (r"C:\Users\<你的用户名>\AppData\Local\ok-ww\data\apps\ok-ww", "working 文件夹"),
    "bh3": (r"D:\Games\Maa_bbb", "MFW.exe"),
    "starrail": (r"D:\Games\March7thAssistant", "March7th Assistant.exe"),
    "arknights": (r"D:\Games\MAA", "MAA.exe"),
    "azurlane": (r"D:\Games\AlasApp\AzurLaneAutoScript", "alas.py"),
}


def tool_dir_hint(key: str) -> str:
    ex = TOOL_DIR_EXAMPLES.get(key)
    return f"例如 {ex[0]}（文件夹里有 {ex[1]}）" if ex else ""


LAUNCH_BY_HUB = "hub"            # 总控直接运行游戏程序，等窗口就绪后再启动脚本
LAUNCH_VIA_LAUNCHER = "launcher"  # 总控打开官方启动器并点“开始游戏”，等窗口就绪后再启动脚本
LAUNCH_BY_TOOL = "tool"          # 脚本工具自己拉起游戏

LAUNCH_MODE_NAMES = {
    LAUNCH_BY_HUB: "总控直接启动游戏程序",
    LAUNCH_VIA_LAUNCHER: "总控打开启动器并点“开始游戏”",
    LAUNCH_BY_TOOL: "工具自行启动游戏",
}


@dataclass
class GameConfig:
    key: str
    name: str
    adapter: str
    enabled: bool = True
    tool_dir: str = ""
    options: dict = field(default_factory=dict)       # adapter-specific (配置名 / 实例 / 任务)
    launch_mode: str = LAUNCH_BY_TOOL
    game_exe: str = ""                                 # started directly when launch_mode == hub
    game_args: str = ""
    game_processes: list[str] = field(default_factory=list)  # image names used to find/close the game
    window_class: str = ""
    window_title: str = ""                             # substring match, empty = any
    close_cmd: str = ""      # optional graceful close command (e.g. emulator manager "shutdown"), run first
    # Official launcher (launch_mode == launcher)
    launcher_exe: str = ""
    launcher_args: str = ""
    launcher_processes: list[str] = field(default_factory=list)  # process that owns the launcher window
    launcher_window_class: str = ""
    launcher_window_title: str = ""
    launcher_click: str = ""                           # "x,y" relative to the launcher client area
    timeout_min: int = 90
    game_ready_timeout_s: int = 240
    game_ready_delay_s: int = 15     # extra wait after the window is ready (only when the hub started the game)
    close_game_after: bool = True
    close_timeout_s: int = 30


@dataclass
class HubConfig:
    order: list[str] = field(default_factory=list)
    continue_on_error: bool = True
    games: dict[str, GameConfig] = field(default_factory=dict)

    def ordered(self) -> list[GameConfig]:
        keys = [k for k in self.order if k in self.games] + [k for k in self.games if k not in self.order]
        return [self.games[k] for k in keys]


def defaults() -> HubConfig:
    games = [
        GameConfig(
            key="genshin", name="原神", adapter="bettergi",
            tool_dir=r"C:\Program Files\BetterGI\BetterGI",
            options={"onedragon_config": "默认配置"},
            # 默认由总控拉起游戏（BetterGI 自己保存的原神路径可能已失效，见 User/config.json）。
            launch_mode=LAUNCH_BY_HUB,
            game_exe=r"C:\Program Files\miHoYo Launcher\games\Genshin Impact\Genshin Impact Game\YuanShen.exe",
            game_processes=["YuanShen.exe"], window_class="UnityWndClass",
            timeout_min=90,
        ),
        GameConfig(
            key="zzz", name="绝区零", adapter="zzz_onedragon",
            tool_dir="",
            options={"instance": "1"},
            launch_mode=LAUNCH_BY_TOOL,  # 一条龙遇到已存在的游戏窗口会跳过“进入游戏/登录”，只能由它自己开
            game_exe=r"C:\Program Files\miHoYo Launcher\games\ZenlessZoneZero Game\ZenlessZoneZero.exe",
            game_processes=["ZenlessZoneZero.exe"], window_class="UnityWndClass",
            timeout_min=120,
        ),
        GameConfig(
            key="endfield", name="终末地", adapter="maaend",
            tool_dir="",
            options={"instance": "快速日常"},
            # MaaEnd 在 PC 上不能开游戏；终末地会检查父进程是否为启动器，所以要经鹰角启动器点“开始游戏”。
            launch_mode=LAUNCH_BY_TOOL,
            game_exe=r"C:\Program Files\Hypergryph Launcher\games\Endfield Game\Endfield.exe",
            game_args="-launcher_lang=zh-cn -launcher_sub_channel=1",   # what 鹰角启动器 passes
            game_processes=["Endfield.exe"], window_class="UnityWndClass",
            launcher_exe=r"C:\Program Files\Hypergryph Launcher\Launcher.exe",
            launcher_processes=["Games.exe"], launcher_window_class="Qt5158QWindowIcon",
            launcher_window_title="鹰角启动器", launcher_click="0.86,0.89",
            timeout_min=60, game_ready_delay_s=30,
        ),
        GameConfig(
            key="wuwa", name="鸣潮", adapter="okww",
            tool_dir=os.path.join(_LOCAL, "ok-ww", "data", "apps", "ok-ww"),
            options={"task": "src.task.DailyTask.DailyTask"},
            # ok-ww would launch with its "Game Package" (hd) but this PC has the uhd package; the hub
            # starts the game exactly like the official launcher does and ok-ww attaches to the window.
            launch_mode=LAUNCH_BY_HUB,
            game_exe=r"C:\Program Files\Wuthering Waves\Wuthering Waves Game\Wuthering Waves.exe",
            game_args="-krqlv=uhd --game Aki",
            game_processes=["Client-Win64-Shipping.exe", "Wuthering Waves.exe"],
            window_class="UnrealWindow",
            # The client often restarts itself ~2 min after launch (10-11 s without a window); start ok-ww after that.
            game_ready_delay_s=150,
            timeout_min=60,
        ),
        GameConfig(
            key="bh3", name="崩坏3", adapter="maabbb",
            tool_dir="",
            options={"config": "日常(每天都能做的任务)"},
            launch_mode=LAUNCH_BY_HUB,   # Maa_bbb does not start the PC client
            game_exe=r"C:\Program Files\miHoYo Launcher\games\Honkai Impact 3rd Game\BH3.exe",
            game_processes=["BH3.exe"], window_class="UnityWndClass",
            timeout_min=60, game_ready_delay_s=20,
        ),
        GameConfig(
            key="starrail", name="星穹铁道", adapter="march7th",
            tool_dir="",
            options={"task": "main"},
            launch_mode=LAUNCH_BY_TOOL,  # it skips "点击进入" when the game is already open
            game_exe=r"C:\Program Files\miHoYo Launcher\games\Star Rail\Game\StarRail.exe",
            game_processes=["StarRail.exe"], window_class="UnityWndClass",
            timeout_min=150,             # its own start-up retries alone can take ~70 min in the worst case
        ),
        GameConfig(
            key="arknights", name="明日方舟", adapter="maa",
            tool_dir="",
            options={"config": "Default"},
            launch_mode=LAUNCH_BY_HUB,   # the "game client" is MuMu Player 12 (instance 0), started like Alas does
            game_exe=r"C:\Program Files\Netease\MuMu Player 12\nx_main\MuMuManager.exe",
            game_args="api -v 0 launch_player",
            game_processes=["MuMuNxDevice.exe"],
            close_cmd='"C:\\Program Files\\Netease\\MuMu Player 12\\nx_main\\MuMuManager.exe" api -v 0 shutdown_player',
            timeout_min=90, game_ready_delay_s=45, close_timeout_s=60,
        ),
        GameConfig(
            key="azurlane", name="碧蓝航线", adapter="alas",
            tool_dir="",
            # hires_patch: optional run_alas.py of a high-resolution patch, relative to tool_dir; empty = plain Alas.
            options={"config": "alas", "hires_patch": ""},
            launch_mode=LAUNCH_BY_HUB,   # MuMu Player 12 instance 0, the same call Alas itself uses
            game_exe=r"C:\Program Files\Netease\MuMu Player 12\nx_main\MuMuManager.exe",
            game_args="api -v 0 launch_player",
            game_processes=["MuMuNxDevice.exe"],
            close_cmd='"C:\\Program Files\\Netease\\MuMu Player 12\\nx_main\\MuMuManager.exe" api -v 0 shutdown_player',
            timeout_min=180, game_ready_delay_s=45, close_timeout_s=60,
        ),
    ]
    return HubConfig(order=[g.key for g in games], games={g.key: g for g in games})


def load(path: Path = CONFIG_PATH) -> HubConfig:
    base = defaults()
    if not path.exists():
        return base
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return base
    base.order = [k for k in raw.get("order", base.order) if k in base.games] or base.order
    base.continue_on_error = bool(raw.get("continue_on_error", base.continue_on_error))
    known = GameConfig.__dataclass_fields__
    for key, data in (raw.get("games") or {}).items():
        if key not in base.games or not isinstance(data, dict):
            continue
        g = base.games[key]
        for k, v in data.items():
            if k in known and k not in ("key", "adapter"):
                if k == "options" and isinstance(v, dict):
                    g.options.update(v)
                else:
                    setattr(g, k, v)
    return base


def save(cfg: HubConfig, path: Path = CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "order": cfg.order,
        "continue_on_error": cfg.continue_on_error,
        "games": {k: asdict(g) for k, g in cfg.games.items()},
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
