"""Alas (碧蓝航线) daily settings: config/<name>.json, built at load time from Alas's own schema.

Evidence: docs/research/settings/azurlane.json (spec + verifier corrections) and the files themselves.
- Schema: module/config/argument/args.json (task -> group -> arg -> {type, value, option, option_<server>,
  option_bold, display, valuetype, validate}), menu.json (sidebar groups; page=tool groups are run-once tools,
  not settings) and module/config/i18n/zh-CN.json (labels: Task.<t>.name, <Group>._info.name,
  <Group>.<Arg>.name/help/<str(option)>).  Read on every open, so options added by an Alas update show up
  with Alas's own labels; a snapshot of the group/arg keys at verified_version reports what changed.
- The page mirrors the WebUI (webui/app.py set_group): display=hide skipped, display=disabled read-only,
  lock/state shown read-only, Campaign.Event uses option_<server> (server from Alas.Emulator.PackageName via
  module/config/server.py), a select with one bold option is a state, GemsFarming's one-option Event skipped.
- The user config is written by Alas with json.dumps(indent=2, ensure_ascii=False), UTF-8, LF, no trailing
  newline: JsonFile reproduces it byte for byte.  Alas normalises it on every read (config_updater.py
  config_update): hidden/lock/state args reset to defaults, select values outside the option list reset,
  unknown keys migrated or dropped.  So hidden and scheduler-internal args are never written here.
- 'input' args change type at runtime (EventDaily.LastStage 0 vs 'a3', JoinThreshold 1 vs 0.5): they are
  shown as text and typed back like the WebUI's parse_pin_value ('' = default, numbers -> int/float).
  Emotion *Value edits also set *Record = now (config_updater.py save_callback).
- Run order is Alas's SCHEDULER_PRIORITY, not the file order: nothing is movable.
- Emulator connection, push notification (OnePushConfig may hold tokens) and drop-record IDs are not shown.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import adapters
from ..config import GameConfig
from .base import Field, JsonFile, Model, Section, SettingsError, SettingsProvider, set_path

VERIFIED_ARGS_SHA256 = "2c4766a5d61a2b75f9d007a36de68d5b02e19c929f332ebd360176b70d755470"
SCHED = "Scheduler"
SKIP_GROUPS = {"Storage"}                              # Storage.Storage: Alas's own memo (dict)
DENY_GROUPS = {"Emulator", "EmulatorInfo", "DropRecord"}   # connection settings / azur-stats ID
DENY_ARGS = {"Error.OnePushConfig"}                    # push config can hold tokens
# Scheduler internals: reset by Alas on every read (hidden) or owned by the scheduler; never written.
NEVER_WRITE = {"Scheduler.Command", "Scheduler.SuccessInterval", "Scheduler.FailureInterval",
               "Scheduler.ServerUpdate", "Scheduler.NextRun"}
# Visible args that Alas itself keeps up to date (help: 自动更新的数值 ...): shown read-only.
STATE_ARGS = {"EventDaily.LastStage", "Exercise.OpponentRefreshValue", "OpsiExplore.LastZone",
              "IslandOrder.StuckSeasonOrderId", "EquipmentCode.Config"}

SORTIE = "出击消耗石油"
TASK_RISK = {
    **{t: SORTIE for t in ("Main", "Main2", "Main3", "Event", "Event2", "Raid", "Hospital", "Coalition",
                           "WarArchives", "EventA", "EventB", "EventC", "EventD", "EventSp", "RaidDaily",
                           "CoalitionSp", "Hard")},
    "GemsFarming": "刷紧急委托：会自动换旗舰/前排并退役换下的白皮航母，持续消耗石油",
    "Tactical": "消耗技能书和物资",
    "Awaken": "认知觉醒直到心智单元耗尽（也花物资）",
    "Gacha": "建造消耗魔方和物资",
    "EventShop": "花活动代币",
    "OpsiArchive": "用白票买档案坐标",
    "OpsiMeowfficerFarming": "消耗行动力，会开行动力箱子",
    "OpsiHazard1Leveling": "消耗作战补给凭证；Alas 读取配置时会同时打开“短猫相接”",
    "IslandOrder": "消耗岛屿库存", "IslandProduction": "消耗岛屿库存", "IslandBusiness": "消耗岛屿库存",
}
TASK_NOTE = {
    "Commission": "石油溢出时 Alas 会自动买 10 份后宅食物（消耗溢出的石油，不能设置）。",
    "Guild": "“舰队后勤”会领/买大舰队补给；之前禁止领补给的补丁已被 Alas 自动更新冲掉。",
}
FLEET_SHOP = "花舰队币"
RISK = {   # "Group.Arg": what turning it on / raising it spends
    "Campaign.Use2xBook": "两倍石油消耗（并消耗高效作战指令书）",
    "HpControl.UseEmergencyRepair": "消耗维修工具",
    "Raid.UseTicket": "消耗共斗活动的门票",
    "Commission.DoMajorCommission": "消耗石油（1200/1000 油委托）",
    "Research.UseCube": "消耗魔方", "Research.UseCoin": "消耗物资", "Research.UsePart": "消耗部件",
    "BuyFurniture.Enable": "花家具币（不买钻石家具）",
    "Meowfficer.BuyAmount": "第一个免费，之后每个 1500 物资",
    "Meowfficer.OverflowCoins": "物资溢出时继续花物资买猫箱",
    "MeowfficerTrain.Enable": "消耗指挥喵作强化材料",
    "GuildLogistics.Enable": "提交筹备物资（消耗部件等），会领/买大舰队补给",
    "GuildOperation.SelectNewOperation": "开启大舰队作战（消耗舰队资源）",
    "GeneralShop.UseGems": "花钻石（付费货币）", "GeneralShop.Refresh": "每次刷新 50 钻石",
    "GeneralShop.BuySkinBox": "花 7000 物资", "GeneralShop.ConsumeCoins": "物资多于 55 万时买光",
    "GeneralShop.Filter": "按过滤器花物资",
    "MeritShop.Refresh": "每次刷新 20 钻石", "MeritShop.BuyUnobtainedShip": "花功勋", "MeritShop.Filter": "花功勋",
    "GuildShop.Refresh": "每次刷新 50 舰队币", "GuildShop.Filter": FLEET_SHOP,
    "MedalShop2.Filter": "花勋章", "CoreShop.Filter": "花核心数据",
    "Shipyard.BuyAmount": "每天前两张免费，之后按递增价格花物资（金图 150→1500）",
    "ShipyardDr.BuyAmount": "每天前两张免费，之后按递增价格花物资（彩图 600→6000）",
    "Gacha.Amount": "建造消耗魔方和物资", "Gacha.Pool": "建造消耗魔方和物资", "Gacha.UseDrill": "消耗快速建造工具",
    "PrivateQuarters.BuyRoses": "每周约 2.4 万物资", "PrivateQuarters.BuyCake": "每周 210 钻石",
    "EventShop.BuyURShip": "花活动代币", "EventShop.PresetFilter": "花活动代币", "EventShop.CustomFilter": "花活动代币",
    "OpsiGeneral.BuyActionPointLimit": "用石油买行动力（每天 1000~10000 油）",
    "OpsiGeneral.AkashiShopFilter": "在明石商店花大世界货币", "OpsiGeneral.DoRandomMapEvent": "在明石商店花大世界货币",
    "OpsiGeneral.UseLogger": "用掉所有坐标记录仪",
    "OpsiShop.PresetFilter": "花大世界港口货币", "OpsiShop.CustomFilter": "花大世界港口货币",
    "OpsiVoucher.Filter": "花白票",
    "OpsiDaily.UseTuningSample": "消耗效能样本",
    "OpsiExplore.ForceRun": "额外消耗行动力", "OpsiObscure.ForceRun": "额外消耗行动力",
    "OpsiAbyssal.ForceRun": "额外消耗行动力", "OpsiStronghold.ForceRun": "额外消耗行动力",
    "OpsiMonthBoss.ForceRun": "额外消耗行动力",
    "OpsiAshBeacon.EnsureFullyCollected": "收集满之前无视保留行动力设置，会多花行动力",
    "Emotion.Mode": "选“无视红脸出击警告”时会红脸出击（掉好感）",
    "GemsFarming.ChangeVanguard": "选“不更换”时会红脸出击（掉好感）",
    "Awaken.LevelCap": "升到 125 级要更多心智单元和物资",
    "Retirement.RetireMode": "会退役舰船或拿舰船当强化材料",
    "OldRetire.SR": "退役紫色舰船", "OldRetire.SSR": "退役金色舰船",
    "OneClickRetire.KeepLimitBreak": "选“不保留”时会退役满破所需的同名舰",
}
# config_update: Event/WarArchives stage 7-2 or 12-4 becomes D3, Coalition stage becomes area1-normal.
STAGE_REWRITE = {**{t: "D3" for t in ("Event", "Event2", "EventA", "EventB", "EventC", "EventD", "EventSp",
                                      "WarArchives")},
                 "Coalition": "area1-normal", "CoalitionSp": "area1-normal"}

# Group -> args at verified_version (generated from args.json); new / removed keys are reported.
KNOWN_ARGS = {
    'AddNewStudent': 'Enable Favorite MinLevel',
    'Awaken': 'Favourite LevelCap',
    'AzurLaneUncensored': 'Repository',
    'BattlePass': 'Collect',
    'Benchmark': 'DeviceType TestScene',
    'BuyFurniture': 'BuyOption Enable LastRun',
    'Campaign': 'AmbushEvade Event Mode Name Use2xBook UseAutoSearch UseClearMode UseFleetLock',
    'Coalition': 'Fleet Mode',
    'Commission': 'CustomFilter DoMajorCommission PresetFilter',
    'ControlExpOverflow': 'Enable T1Allow T2Allow T3Allow T4Allow',
    'CoreShop': 'Filter',
    'Daemon': 'EnterMap',
    'Daily': ('AdvanceMission AdvanceMissionFleet EmergencyModuleDevelopment EmergencyModuleDevelopmentFleet '
              'EscortMission EscortMissionFleet FierceAssault FierceAssaultFleet ModuleDevelopment '
              'ModuleDevelopmentFleet SupplyLineDisruption TacticalTraining TacticalTrainingFleet UseDailySkip'),
    'DataKey': 'Collect ForceCollect',
    'Dorm': 'Collect Feed FeedFilter',
    'DropRecord': ('API AzurStatsID CombatRecord CommissionRecord MeowfficerBuy MeowfficerTalent OpsiRecord '
                   'ResearchRecord SaveFolder'),
    'Emotion': ('Fleet1Control Fleet1Oath Fleet1Record Fleet1Recover Fleet1Value Fleet2Control Fleet2Oath '
                'Fleet2Record Fleet2Recover Fleet2Value Mode'),
    'Emulator': 'AdbRestart ControlMethod PackageName ScreenshotDedithering ScreenshotMethod Serial ServerName',
    'EmulatorInfo': 'Emulator name path',
    'EnemyPriority': 'EnemyScaleBalanceWeight',
    'Enhance': 'CheckPerCategory Filter ShipToEnhance',
    'EquipmentCode': 'Config ExportToConfig',
    'Error': 'HandleError OnePushConfig SaveError ScreenshotLength',
    'EventDaily': 'LastStage StageFilter',
    'EventGeneral': 'PtLimit TimeLimit',
    'EventShop': 'BuyURShip CustomFilter PresetFilter UnlockSSRShip',
    'EventStory': 'SkipBattle',
    'Exercise': ('ExerciseStrategy LowHpConfirmWait LowHpThreshold OpponentChooseMode OpponentRefreshRecord '
                 'OpponentRefreshValue OpponentTrial'),
    'Fleet': 'Fleet1 Fleet1Formation Fleet1Mode Fleet1Step Fleet2 Fleet2Formation Fleet2Mode Fleet2Step FleetOrder',
    'Gacha': 'Amount Pool UseDrill UseTicket',
    'GameManager': 'AutoRestart',
    'GemsFarming': 'ChangeFlagship ChangeVanguard CommissionLimit CommonCV CommonDD',
    'GeneralShop': 'BuySkinBox ConsumeCoins Filter Refresh UseGems',
    'GuildLogistics': 'Enable ExchangeFilter SelectNewMission',
    'GuildOperation': 'AttackBoss BossFleetRecommend Enable JoinThreshold NewOperationMaxDate SelectNewOperation',
    'GuildShop': ('BOOK_T2 BOOK_T3 BOX_T3 BOX_T4 Filter PLATE_T2 PLATE_T3 PLATE_T4 PR1 PR2 PR3 RETROFIT_T2 '
                  'RETROFIT_T3 Refresh'),
    'Hard': 'HardFleet HardStage',
    'Hospital': 'UseRecommendFleet',
    'HpControl': ('HpBalanceThreshold HpBalanceWeight LowHpRetreatThreshold RepairUseMultiThreshold '
                  'RepairUseSingleThreshold UseEmergencyRepair UseHpBalance UseLowHpRetreat'),
    'IslandFreebie': 'Share',
    'IslandOrder': 'StuckSeasonOrderId',
    'IslandProduction': 'DailyBufferItems HardFloorItems IdleAccumulatingItems ManualBufferItems SkipBufferSurplus',
    'IslandProductionPlanner': ('DailyBufferSafetyMargin DailyProfitLowerLimit FieldsEfficiency '
                                'NurseryEfficiency OrchardEfficiency RescanIslandTechnology'),
    'IslandRestaurant': ('BearGrade BearMenu BearWaitress1 BearWaitress2 CafeGrade CafeMenu CafeWaitress1 '
                         'CafeWaitress2 EateryGrade EateryMenu EateryWaitress1 EateryWaitress2 GrillGrade '
                         'GrillMenu GrillWaitress1 GrillWaitress2 KoiGrade KoiMenu KoiWaitress1 KoiWaitress2'),
    'IslandSeasonTask': 'TaskTarget',
    'Mail': 'ClaimMaintenance ClaimMerit ClaimTradeLicense DeleteCollected',
    'MedalShop2': 'Filter PLATE_T1 PLATE_T2 PLATE_T3 RETROFIT_T1 RETROFIT_T2 RETROFIT_T3',
    'Meowfficer': 'BuyAmount FortChoreMeowfficer OverflowCoins',
    'MeowfficerTrain': 'Enable EnhanceIndex MaxFeedLevel Mode RetainTalentedGold RetainTalentedPurple',
    'MeritShop': 'BuyUnobtainedShip Filter Refresh',
    'OldRetire': 'N R RetireAmount SR SSR',
    'OneClickRetire': 'KeepLimitBreak',
    'OpsiAbyssal': 'ForceRun',
    'OpsiAshAssist': 'Tier',
    'OpsiAshBeacon': 'AttackMode DossierAutoAttackMode EnsureFullyCollected OneHitMode RequestAssist',
    'OpsiDaemon': 'RepairShip SelectEnemy',
    'OpsiDaily': 'DoMission UseTuningSample',
    'OpsiExplore': 'ForceRun LastZone SpecialRadar',
    'OpsiFleet': 'Fleet Submarine',
    'OpsiFleetFilter': 'Filter',
    'OpsiGeneral': 'AkashiShopFilter BuyActionPointLimit DoRandomMapEvent OilLimit RepairThreshold UseLogger',
    'OpsiHazard1Leveling': 'TargetZone',
    'OpsiMeowfficerFarming': 'ActionPointPreserve HazardLevel TargetZone',
    'OpsiMonthBoss': 'CheckAdaptability ForceRun Mode',
    'OpsiObscure': 'ForceRun',
    'OpsiShop': 'CustomFilter PresetFilter',
    'OpsiStronghold': 'ForceRun',
    'OpsiVoucher': 'Filter',
    'Optimization': 'CombatScreenshotInterval ScreenshotInterval TaskHoardingDuration WhenTaskQueueEmpty',
    'PrivateQuarters': 'BuyCake BuyRoses TargetInteract TargetShip',
    'Raid': 'Mode UseTicket',
    'RaidDaily': 'StageFilter',
    'Research': 'AllowDelay CustomFilter PresetFilter UseCoin UseCube UsePart',
    'Retirement': 'RetireMode',
    'Reward': 'CollectCoin CollectExp CollectMission CollectOil CollectWeeklyMission',
    'Scheduler': 'Command Enable FailureInterval NextRun ServerUpdate SuccessInterval',
    'Shipyard': 'BuyAmount LastRun ResearchSeries ShipIndex',
    'ShipyardDr': 'BuyAmount LastRun ResearchSeries ShipIndex',
    'StopCondition': 'GetNewShip MapAchievement OilLimit ReachLevel RunCount StageIncrease',
    'Storage': 'Storage',
    'Submarine': 'AutoSearchMode DistanceToBoss Fleet Mode',
    'SupplyPack': 'Collect DayOfWeek',
    'Tactical': 'RapidTrainingSlot TacticalFilter',
    'TaskBalancer': 'CoinLimit Enable TaskCall',
}
KNOWN_TASKS = frozenset("""
Alas Awaken AzurLaneUncensored Benchmark Coalition CoalitionSp Commission Daemon Daily Dorm Event Event2
EventA EventB EventC EventD EventGeneral EventShop EventSp EventStory Exercise Freebies Gacha GameManager
GemsFarming General Guild Hard Hospital IslandBusiness IslandCollect IslandFreebie IslandOrder
IslandProduction IslandProductionPlanner IslandSeasonTask Main Main2 Main3 Meowfficer Minigame OpsiAbyssal
OpsiArchive OpsiAshAssist OpsiAshBeacon OpsiCrossMonth OpsiDaemon OpsiDaily OpsiExplore OpsiGeneral
OpsiHazard1Leveling OpsiMeowfficerFarming OpsiMonthBoss OpsiObscure OpsiShop OpsiStronghold OpsiVoucher
PrivateQuarters Raid RaidDaily Research Restart Reward Shipyard ShopFrequent ShopOnce Tactical WarArchives
""".split())


@dataclass
class AlasField(Field):
    spec: dict = field(default_factory=dict)   # this arg's args.json entry
    scalar: bool = False                       # Alas 'input': shown as text, typed back like the WebUI
    pair: str = ""                             # Emotion *Value: the *Record arg set to now on edit

    def accepts(self, value) -> bool:
        if self.scalar:
            return not isinstance(value, (dict, list))
        return super().accepts(value)


def _pin_value(text, spec: dict):
    """webui/utils.py parse_pin_value plus the save rule '' -> args.json default."""
    if not isinstance(text, str):
        return text
    if text.strip() == "":
        return spec.get("value")
    if spec.get("valuetype") == "str":
        return text
    try:
        v = float(text)
    except ValueError:
        return text
    if v != v or v in (float("inf"), float("-inf")):
        return text
    return int(v) if v.is_integer() else v


def _read_json(path: str) -> dict:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise SettingsError(f"读不了 Alas 的 {os.path.basename(path)}：{e}") from e
    if not isinstance(d, dict):
        raise SettingsError(f"Alas 的 {os.path.basename(path)} 格式不对")
    return d


def _servers(tool_dir: str) -> dict:
    """module/config/server.py VALID_PACKAGE / VALID_CHANNEL_PACKAGE (package -> server), parsed as text."""
    out = {}
    try:
        tree = ast.parse(Path(tool_dir, "module", "config", "server.py").read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                    and node.targets[0].id in ("VALID_PACKAGE", "VALID_CHANNEL_PACKAGE"):
                for k, v in ast.literal_eval(node.value).items():
                    out[k] = v[0] if isinstance(v, tuple) else v
    except (OSError, SyntaxError, ValueError):
        pass
    return out


def _last_update(tool_dir: str) -> str:
    """Time of the last reflog entry that moved HEAD (Alas resets to origin/master on every launch)."""
    try:
        with open(os.path.join(tool_dir, ".git", "logs", "HEAD"), "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 65536))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        parts = line.split(" ", 2)
        m = re.search(r" (\d{9,11}) [+-]\d{4}\t", line)
        if len(parts) == 3 and parts[0] != parts[1] and m:
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(m.group(1))))
    return ""


class AlasSettings(SettingsProvider):
    key = "alas"
    verified_version = "7d6a415e3"

    @staticmethod
    def path(g: GameConfig, name: str) -> str:
        return os.path.join(g.tool_dir, "config", f"{name}.json")

    def config_names(self, g):
        return adapters.get(g.adapter).option_choices(g).get("config", [])   # excludes *.maa/*.fpy, template

    def default_config(self, g):
        names = self.config_names(g)
        name = str(g.options.get("config", "")).strip() or "alas"
        return name if name in names else ("alas" if "alas" in names else (names[0] if names else ""))

    def file_paths(self, g):
        return [self.path(g, n) for n in self.config_names(g)]

    def busy(self, g):
        return adapters.get(g.adapter).running_instances(g)

    # ---- load ----
    def load(self, g, config_name):
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", config_name or ""):
            raise SettingsError(f"Alas 配置名「{config_name}」无效")
        path = self.path(g, config_name)
        if not os.path.isfile(path):
            raise SettingsError(f"找不到 {path}")
        arg_dir = os.path.join(g.tool_dir, "module", "config", "argument")
        args_path = os.path.join(arg_dir, "args.json")
        args = _read_json(args_path)
        menu = _read_json(os.path.join(arg_dir, "menu.json"))
        i18n = _read_json(os.path.join(g.tool_dir, "module", "config", "i18n", "zh-CN.json"))
        f = JsonFile(path)
        data = f.data
        if not isinstance(data, dict):
            raise SettingsError(f"{os.path.basename(path)} 格式不对")
        model = Model(files=[f], sections=[])
        self._t = i18n

        pkg = ((data.get("Alas") or {}).get("Emulator") or {}).get("PackageName", "cn")
        server = pkg if pkg in ("cn", "en", "jp", "tw") else _servers(g.tool_dir).get(pkg, "cn")
        self._check_schema(model, args, data, Path(args_path).read_bytes())

        placed = set()
        for mg, m in menu.items():
            if not isinstance(m, dict) or m.get("page") == "tool":
                continue
            gname = self._name("Menu", mg) or mg
            for task in m.get("tasks", []):
                if task in args and task not in placed:
                    placed.add(task)
                    model.sections.append(self._section(model, args, data, task, gname, server))
        for task in args:                    # a task the menu does not list (should not happen)
            if task not in placed and task not in {t for m in menu.values() for t in m.get("tasks", [])}:
                model.sections.append(self._section(model, args, data, task, "其他", server))

        if not f.exact:
            model.notes.append("这个文件的格式无法原样写回，只能查看，请在 Alas 里修改。")
        model.notes.append("运行顺序由 Alas 按自己的优先级决定，这里不能调整；改动在 Alas 下次读取配置时生效。")
        model.notes.append("Alas 读取配置时会自己整理：隐藏项和强制项恢复默认，已结束的活动换成最新活动。"
                           "模拟器连接、推送通知、掉落统计请在 Alas 里改，这里不显示。")
        when = _last_update(g.tool_dir)
        if when:
            model.notes.append(f"Alas 上次自动更新：{when}")
        return model

    def _name(self, *path) -> str:
        cur = self._t
        for p in path:
            if not isinstance(cur, dict) or p not in cur:
                return ""
            cur = cur[p]
        if not isinstance(cur, dict):
            return ""
        s = cur.get("name", "")
        return "" if not isinstance(s, str) or s == ".".join(path) + ".name" else s

    def _text(self, *path) -> str:
        cur = self._t
        for p in path:
            if not isinstance(cur, dict) or p not in cur:
                return ""
            cur = cur[p]
        return cur if isinstance(cur, str) and cur != ".".join(path) else ""

    def _check_schema(self, model: Model, args: dict, data: dict, raw: bytes) -> None:
        if hashlib.sha256(raw).hexdigest() != VERIFIED_ARGS_SHA256:
            model.drift.append("Alas 的选项定义（args.json）和核对时不同：新选项按 Alas 自己的说明显示，"
                               "但总控没有核对它们是否消耗资源")
        known = {f"{g}.{a}" for g, s in KNOWN_ARGS.items() for a in s.split()}
        now = {f"{g}.{a}" for t in args.values() if isinstance(t, dict)
               for g, A in t.items() if isinstance(A, dict) for a in A}
        gone = sorted(known - now)
        if gone:
            model.drift.append(f"这些选项已不在 Alas 里：{'、'.join(gone[:20])}{'…' if len(gone) > 20 else ''}")
        new_tasks = sorted(set(args) - KNOWN_TASKS)
        if new_tasks:
            model.drift.append(f"Alas 新增了任务：{'、'.join(new_tasks)}（未核对是否消耗资源）")
        stale = sorted(k for k in (set(RISK) | STATE_ARGS | NEVER_WRITE | DENY_ARGS) if k not in now)
        stale += sorted(t for t in set(TASK_RISK) | set(TASK_NOTE) | set(STAGE_REWRITE) if t not in args)
        if stale:
            model.drift.append(f"总控说明表里的这些项已不在 Alas 里：{'、'.join(stale)}")
        for task, groups in data.items():
            if task not in args:
                model.drift.append(f"配置里的任务 {task} 已不在 Alas 里（Alas 下次读取时会迁移或丢弃）")
            elif isinstance(groups, dict):
                extra = [f"{grp}.{a}" for grp, A in groups.items() if isinstance(A, dict)
                         for a in A if a not in (args[task].get(grp) or {})]
                extra += [grp for grp, A in groups.items() if grp not in args[task]]
                if extra:
                    model.drift.append(f"{task}：配置里有 Alas 已不认识的选项 {'、'.join(extra[:10])}"
                                       "（Alas 下次读取时会迁移或丢弃）")
        self._new_args = now - known

    def _section(self, model: Model, args: dict, data: dict, task: str, group: str, server: str) -> Section:
        schema = args[task] if isinstance(args[task], dict) else {}
        attached = isinstance(data.get(task), dict)
        obj = data[task] if attached else {}
        title = self._name("Task", task) or task
        sched = schema.get(SCHED, {}).get("Enable") if isinstance(schema.get(SCHED), dict) else None
        switch = (isinstance(sched, dict) and sched.get("type") == "checkbox"
                  and sched.get("display") not in ("hide", "disabled") and attached)
        fields: list[Field] = []
        for grp, arg_specs in schema.items():
            if grp in SKIP_GROUPS or grp in DENY_GROUPS or not isinstance(arg_specs, dict):
                continue
            first = True
            for arg, spec in arg_specs.items():
                if not isinstance(spec, dict) or (switch and grp == SCHED and arg == "Enable"):
                    continue
                fd = self._field(model, task, grp, arg, spec, arg_specs, obj, server, title)
                if fd is None:
                    continue
                if first:
                    info = self._text(grp, "_info", "help")
                    if info:
                        fd.help = (f"【{self._name(grp, '_info') or grp}】{info}\n" + fd.help).strip()
                    first = False
                fields.append(fd)
        notes = [self._text("Task", task, "help"), TASK_NOTE.get(task, "")]
        if task in TASK_RISK:
            notes.insert(0, f"⚠ 打开后：{TASK_RISK[task]}")
        if task not in KNOWN_TASKS:
            notes.append("Alas 更新后新出现的任务，总控还没有核对它是否消耗资源。")
        if not attached:
            notes.append("配置文件里还没有这个任务，显示的是 Alas 的默认值；改动后会写进配置。")
        nxt = (obj.get(SCHED) or {}).get("NextRun") if isinstance(obj.get(SCHED), dict) else None
        on = (obj.get(SCHED) or {}).get("Enable") if isinstance(obj.get(SCHED), dict) else None
        sub = f"下次 {str(nxt)[:16]}" if on and isinstance(nxt, str) and nxt else ""
        return Section(task, title, obj, fields, enable_key=(SCHED, "Enable") if switch else None,
                       group=group, subtitle=sub, note="\n".join(n for n in notes if n),
                       on_set=self._setter(data, task))

    def _field(self, model: Model, task: str, grp: str, arg: str, spec: dict, group_specs: dict, obj: dict,
               server: str, title: str) -> AlasField | None:
        key = f"{grp}.{arg}"
        typ, display = spec.get("type"), spec.get("display")
        if display == "hide" or key in DENY_ARGS or typ in ("storage",):
            return None
        options = spec.get("option") if isinstance(spec.get("option"), list) else None
        if typ == "select" and isinstance(spec.get(f"option_{server}"), list) and spec[f"option_{server}"]:
            options = spec[f"option_{server}"]
        if task == "GemsFarming" and key == "Campaign.Event" and options is not None and len(options) == 1:
            return None
        label = f"{self._name(grp, '_info') or grp} › {self._name(grp, arg) or arg}"
        help_ = self._text(grp, arg, "help")
        readonly = display == "disabled" or key in NEVER_WRITE or key in STATE_ARGS or typ in ("lock", "state")
        bold = spec.get("option_bold")
        if typ == "select" and options and len(options) == 1 and isinstance(bold, list) and options[0] in bold:
            readonly = True
        kind, scalar = "text", False
        choices: list = []
        if typ == "checkbox":
            kind = "bool"
        elif typ in ("select", "lock", "state") and options:
            kind = "choice"
            for o in options:                   # option values verbatim (int stays int)
                choices.append((o, self._text(grp, arg, str(o)) or str(o)))
        elif typ == "textarea":
            kind = "textarea"
        elif typ in ("input", "datetime", "stored", "select", "lock", "state"):
            scalar = True
            readonly = readonly or typ != "input"   # datetimes are scheduler/record state; written by Alas
        else:
            scalar, readonly = True, True
            model.drift.append(f"{title}：「{label}」是总控不认识的类型 {typ}，只读")
        if key in STATE_ARGS and not help_:
            help_ = "Alas 自动维护的数值"
        if task in STAGE_REWRITE and key == "Campaign.Name":
            help_ = (help_ + "\n" if help_ else "") + f"填 7-2 或 12-4 时，Alas 读取配置会自动改成 {STAGE_REWRITE[task]}。"
        pair = ""
        if grp == "Emotion" and "Value" in arg and not readonly:
            pair = arg.replace("Value", "Record")
            pair = pair if pair in group_specs else ""
            if pair:
                help_ = (help_ + "\n" if help_ else "") + "修改后会同时把记录时间设为现在（与 Alas 网页版相同）。"
        if key in self._new_args:
            help_ = (help_ + "\n" if help_ else "") + "（Alas 更新后新出现的选项，总控还没有核对它是否消耗资源）"
        cur = obj.get(grp, {}).get(arg) if isinstance(obj.get(grp), dict) else None
        if kind == "choice" and cur is not None and cur not in [c[0] for c in choices]:
            model.drift.append(f"{title}：「{label}」当前值 {cur!r} 不在 Alas 现在的选项里，Alas 可能会把它改掉")
        return AlasField((grp, arg), label, kind, choices, help=help_, risk=RISK.get(key, ""),
                         readonly=readonly, default=spec.get("value"), spec=spec, scalar=scalar, pair=pair)

    @staticmethod
    def _setter(data: dict, task: str):
        def on_set(obj: dict, fd: Field, value) -> None:
            if data.get(task) is not obj:           # task missing from the file: attach it on first edit
                data[task] = obj
            if isinstance(fd, AlasField) and fd.scalar:
                value = _pin_value(value, fd.spec)
            set_path(obj, fd.key, value)
            if isinstance(fd, AlasField) and fd.pair:
                set_path(obj, (fd.key[0], fd.pair), time.strftime("%Y-%m-%d %H:%M:%S"))
        return on_set
