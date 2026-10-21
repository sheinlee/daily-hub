"""MAA (明日方舟) daily settings: config/gui.new.json → Configurations.<name>.

Evidence: docs/research/phase1/misc-tools.md (MAA part) and the files themselves.
- gui.new.json is the live config (gui.json is the old format; where they differ MAA follows gui.new.json).
  It is UTF-8 without BOM, CRLF, 2-space indent, and json.dumps(indent=2, ensure_ascii=False) reproduces
  it byte for byte, so a save only changes the edited values.
- The daily tasks are Configurations.<name>.TaskQueue: a list of typed objects ($type FightTask, ...),
  run in list order, each with Name / IsEnable.  RunDirectly and PostActions live in .Gui.
- MAA rewrites the file while it runs and when it exits -> read-only while MAA.exe runs.
- Labels follow MAA's own zh-cn strings (MAA.dll, Res/Localizations/zh-cn.xaml).  Enum values offered
  are only ones seen in this config or used by MaaCore (drones); anything else is shown as-is, not offered.
"""
from __future__ import annotations

import os

from .. import adapters
from ..adapters.base import read_json
from ..config import GameConfig
from .base import Field, JsonFile, Model, Section, SettingsError, SettingsProvider, has_path

F = Field

TYPE_NAMES = {
    "StartUpTask": "开始唤醒", "RecruitTask": "自动公招", "InfrastTask": "基建换班", "FightTask": "理智作战",
    "MallTask": "信用收支", "AwardTask": "领取奖励", "RoguelikeTask": "自动肉鸽", "ReclamationTask": "生息演算",
    "SwitchThemeTask": "切换主题",
}

GUI_FIELDS = [
    F("StartUpSettings.RunDirectly", "启动后直接运行",
      help="总控用 MAA.exe --config <配置名> 启动 MAA，只有打开这一项 MAA 才会自动开跑。\n"
           "注意：打开后，手动用这个配置打开 MAA 也会直接开跑。"),
    F("PostActions", "完成后", "choice", [("None", "无动作"), ("ExitSelf", "退出 MAA")],
      help="全部任务结束后 MAA 做什么。总控会自己收尾（关 MAA 和模拟器），选“无动作”即可。\n"
           "电脑的睡眠、关机请用总控运行页的“完成后”，不要在 MAA 里设置（总控预检会拦截）。"),
]

STARTUP_FIELDS = [
    F("AccountSwitchEnabled", "账号切换"),
    F("AccountName", "需要切换至的账号", "text", enabled_by="AccountSwitchEnabled",
      help="输入登录界面显示的内容，如 123****4567，也可以只输入能唯一区分的部分（如 4567）。"),
]

RECRUIT_FIELDS = [
    F("MaxTimes", "每次执行时最大招募次数", "int", max=99, risk="消耗招聘许可"),
    F("RefreshLevel3", "自动刷新 3 星 Tags"),
    F("ForceRefresh", "无招聘许可时继续尝试刷新 Tags"),
    F("Level3Choose", "自动确认 3 星"),
    F("Level4Choose", "自动确认 4 星"),
    F("Level5Choose", "自动确认 5 星"),
    F("Level6Choose", "自动确认 6 星"),
    F("Level3Time", "3 星招募时长", "int", min=60, max=540, step=10, suffix=" 分钟", help="540 分钟 = 9 小时"),
    F("Level4Time", "4 星招募时长", "int", min=60, max=540, step=10, suffix=" 分钟", help="540 分钟 = 9 小时"),
    F("Level3RecruitmentPermitReserveEnabled", "3 星保留招聘许可"),
    F("Level3RecruitmentPermitReserve", "保留数量", "int", max=999,
      enabled_by="Level3RecruitmentPermitReserveEnabled", help="当剩余招聘许可小于等于该值时跳过 3 星招聘"),
    F("ExtraTagMode", "公招多选 Tag 的策略", "choice",
      [(0, "默认不选择额外 Tag"), (1, "选择高星时总是选择三个 Tag"), (2, "尽可能多地选且只选高星 Tag")]),
    F("PreferTagEnabled", "3 星 Tag 时的 Tag 倾向"),
    F("Level3PreferTags", "倾向的 Tag", "list", enabled_by="PreferTagEnabled",
      help="会尽可能多的选择倾向的 Tag。多个用分号分隔。"),
    F("PreserveTagEnabled", "保留指定词条"),
    F("PreserveTagList", "保留的词条", "list", enabled_by="PreserveTagEnabled",
      help="识别到其中任一词条时跳过这次招募，把位置留着。多个用分号分隔。"),
]

INFRAST_FIELDS = [
    F("Mode", "基建模式", "choice", [("Normal", "常规模式"), ("Rotation", "队列轮换")],
      help="使用队列轮换需提前在游戏中设置好队列。自定义基建配置请在 MAA 里设置。"),
    F("UsesOfDrones", "无人机用途", "choice",
      [("_NotUse", "不使用无人机"), ("Money", "贸易站-龙门币"), ("SyntheticJade", "贸易站-合成玉"),
       ("CombatRecord", "制造站-作战记录"), ("PureGold", "制造站-赤金"), ("OriginStone", "制造站-源石碎片"),
       ("Chip", "制造站-芯片组")]),
    F("DormThreshold", "基建工作心情阈值", "int", max=100, suffix=" %"),
    F("DormTrustEnabled", "宿舍空余位置蹭信赖"),
    F("DormFilterNotStationed", "不将已进驻的干员放入宿舍"),
    F("OriginiumShardAutoReplenishment", "源石碎片自动补货", risk="制造站做源石碎片时自动补原料，消耗材料"),
    F("ReceptionMessageBoard", "会客室信息板收取信用"),
    F("ReceptionClueExchange", "进行线索交流"),
    F("SendClue", "赠送线索"),
    F("ContinueTraining", "训练完成后继续尝试专精当前技能"),
]

FIGHT_FIELDS = [
    F("StagePlan", "关卡", "list", empty_label="当前/上次",
      help="可以填多个，用分号分隔，依次作为候选关卡：执行第一个当天开放的关卡。\n"
           "例：1-7、CE-6、LS-6、AP-5、CA-5、SK-5；剿灭填 Annihilation；“当前/上次”= 游戏里上次打的关卡。\n"
           "MAA 下拉框里没有的关卡（如主线 4-10）要同时打开“手动输入关卡名”。"),
    F("UseOptionalStage", "使用备选关卡", help="关卡填了多个时打开"),
    F("IsStageManually", "手动输入关卡名", help="支持大部分主线关卡名与原列表的关卡名（如 4-10、AP-5、H10-1-Hard）"),
    F("Series", "代理倍率", "choice", [(0, "AUTO"), (1, "1"), (2, "2"), (3, "3"), (4, "4"), (5, "5"), (6, "6")],
      help="AUTO：自动识别关卡最大代理倍率，保持最大代理倍率且使用理智药后理智不溢出。"),
    F("UseMedicine", "使用理智药", risk="消耗理智药"),
    F("MedicineCount", "理智药数量", "int", max=999, enabled_by="UseMedicine"),
    F("UseStone", "使用源石", risk="消耗源石（付费货币）",
      help="MAA 默认不保存这一项的勾选（MAA 里的“允许使用源石保存状态”），在这里打开可能会被 MAA 复原。"),
    F("StoneCount", "源石数量", "int", max=999, enabled_by="UseStone"),
    F("UseExpiringMedicine", "无限吃 N 天内过期的理智药", risk="消耗临期理智药"),
    F("MedicineExpireDays", "N（天）", "int", min=1, max=30, enabled_by="UseExpiringMedicine",
      help="只吃游戏内显示不足 N 天的理智药，显示「N 天」及以上的不吃。"),
    F("UseExpireMedicineForActivity", "活动结束前 48H 吃当周过期理智药", risk="消耗理智药"),
    F("IsDrGrandet", "博朗台模式", help="在恢复理智确认界面等待，直到当前的 1 点理智恢复完成再立即确认。"),
    F("EnableTimesLimit", "指定次数"),
    F("TimesLimit", "次数", "int", max=2_147_483_647, enabled_by="EnableTimesLimit"),
    F("EnableTargetDrop", "指定材料"),
    F("DropId", "材料", "choice", enabled_by="EnableTargetDrop"),       # choices filled from item_index.json
    F("DropCount", "刷取数量", "int", max=999_999, enabled_by="EnableTargetDrop"),
]

MALL_FIELDS = [
    F("VisitFriends", "访问好友"),
    F("Shopping", "信用交易所自动购物", risk="花信用点"),
    F("FirstList", "优先购买", "text", enabled_by="Shopping", help="子串即可，分号分隔"),
    F("BlackList", "黑名单", "text", enabled_by="Shopping", help="子串即可，分号分隔"),
    F("ShoppingIgnoreBlackListWhenFull", "信用溢出时无视黑名单", enabled_by="Shopping"),
    F("OnlyBuyDiscount", "只购买打折的信用商品", enabled_by="Shopping"),
    F("ReserveMaxCredit", "信用点低于 300 时停止购买商品", enabled_by="Shopping"),
    F("CreditFight", "借助战打 OF-1 赚信用",
      help="访问好友后借助战打一把 OF-1 赚 30 信用。\n关卡选择为“当前/上次”时此功能无效。"),
]

AWARD_FIELDS = [
    F("Award", "领取每日/每周任务奖励"),
    F("Mail", "领取所有邮件奖励"),
    F("FreeGacha", "进行限定池赠送的每日免费单抽"),
    F("Orundum", "领取幸运墙的每日合成玉奖励"),
    F("Mining", "领取限时开采许可的每日合成玉奖励"),
    F("SpecialAccess", "领取周年赠送月卡奖励"),
    F("SignInEvent", "领取限时签到活动奖励"),
]

# Keys each task type (and .Gui) had in MAA 6.19.0 — new keys after an update are reported, not edited.
KNOWN_KEYS = {
    'AwardTask': frozenset({
        '$type', 'Award', 'FreeGacha', 'IsEnable', 'Mail', 'Mining', 'Name', 'Orundum', 'SignInEvent',
        'SpecialAccess', 'TaskType',
    }),
    'FightTask': frozenset({
        '$type', 'AnnihilationStage', 'DropCount', 'DropId', 'EnableTargetDrop', 'EnableTimesLimit',
        'HideSeries', 'HideUnavailableStage', 'IsDrGrandet', 'IsEnable', 'IsInventoryTarget',
        'IsStageManually', 'MedicineCount', 'MedicineExpireDays', 'Name', 'Series', 'StagePlan',
        'StageResetMode', 'StoneCount', 'TaskType', 'TimesLimit', 'UseCustomAnnihilation',
        'UseExpireMedicineForActivity', 'UseExpiringMedicine', 'UseMedicine', 'UseOptionalStage',
        'UseStone', 'UseStoneAllowSave', 'UseWeeklySchedule',
    }),
    'Gui': frozenset({
        'AchievementPopupAutoClose', 'AchievementPopupDisabled', 'ConnectSettings', 'ExternalNotification',
        'Notification', 'PostActions', 'RemoteControl', 'RuntimeSettings', 'StartUpSettings', 'ThirdParty',
        'WindowTitlePrefix',
    }),
    'InfrastTask': frozenset({
        '$type', 'ContinueTraining', 'CustomFileType', 'DormFilterNotStationed', 'DormThreshold',
        'DormTrustEnabled', 'FiammettaRecoveryEnabled', 'FiammettaTarget1', 'FiammettaTarget2',
        'FiammettaTarget3', 'Filename', 'IsEnable', 'Mode', 'Name', 'OriginiumShardAutoReplenishment',
        'PlanSelect', 'ReceptionClueExchange', 'ReceptionMessageBoard', 'RoomList', 'SendClue', 'TaskType',
        'UseAbyssalHunter', 'UsePerceptionInformation', 'UsePinusSylvestris', 'UseWeeklySchedule',
        'UseWorldlyPlight', 'UsesOfDrones',
    }),
    'MallTask': frozenset({
        '$type', 'BlackList', 'CreditFight', 'CreditFightFormation', 'CreditFightLastTime',
        'CreditFightOnceADay', 'FirstList', 'IsCreditFightAvailable', 'IsEnable',
        'IsVisitFriendsAvailable', 'Name', 'OnlyBuyDiscount', 'ReserveMaxCredit', 'Shopping',
        'ShoppingIgnoreBlackListWhenFull', 'TaskType', 'VisitFriends', 'VisitFriendsLastTime',
        'VisitFriendsOnceADay',
    }),
    'ReclamationTask': frozenset({
        '$type', 'ClearStore', 'IncrementMode', 'IsEnable', 'MaxCraftCountPerRound', 'Mode', 'Name',
        'TaskType', 'Theme', 'ToolToCraft',
    }),
    'RecruitTask': frozenset({
        '$type', 'ExtraTagMode', 'ForceRefresh', 'IsEnable', 'Level3Choose', 'Level3PreferTags',
        'Level3RecruitmentPermitReserve', 'Level3RecruitmentPermitReserveEnabled', 'Level3Time',
        'Level4Choose', 'Level4Time', 'Level5Choose', 'Level6Choose', 'MaxTimes', 'Name',
        'PreferTagEnabled', 'PreserveTagEnabled', 'PreserveTagList', 'RefreshLevel3', 'TaskType',
    }),
    'RoguelikeTask': frozenset({
        '$type', 'BlackFlowCultivationTarget', 'CollectibleShopping', 'CollectibleStartAwards',
        'DeepExplorationAutoIterate', 'Difficulty', 'ExpectedCollapsalParadigms', 'FindPlaytimeTarget',
        'InvestCount', 'InvestWithMoreScore', 'Investment', 'IsEnable', 'Mode', 'MonthlySquadAutoIterate',
        'MonthlySquadCheckComms', 'Name', 'RefreshTraderWithDice', 'Roles', 'SamiFirstFloorFoldartal',
        'SamiFirstFloorFoldartals', 'SamiNewSquad2StartingFoldartal', 'SamiNewSquad2StartingFoldartals',
        'Seed', 'Squad', 'SquadCollectible', 'SquadIsFoldartal', 'StartCount', 'StartWithEliteTwo',
        'StartWithEliteTwoOnly', 'StartWithSeed', 'StartingOpers', 'StopAtFinalBoss',
        'StopWhenDepositFull', 'StopWhenLevelMax', 'TaskType', 'Theme', 'UseAdditionalStartingOpers',
        'UseSupportNonFriend',
    }),
    'StartUpTask': frozenset({
        '$type', 'AccountName', 'AccountSwitchEnabled', 'IsEnable', 'Name', 'TaskType',
    }),
    'SwitchThemeTask': frozenset({
        '$type', 'IsEnable', 'Name', 'TaskType', 'Themes',
    }),
}

TYPE_FIELDS = {
    "StartUpTask": STARTUP_FIELDS, "RecruitTask": RECRUIT_FIELDS, "InfrastTask": INFRAST_FIELDS,
    "FightTask": FIGHT_FIELDS, "MallTask": MALL_FIELDS, "AwardTask": AWARD_FIELDS,
}


class MaaSettings(SettingsProvider):
    key = "maa"
    verified_version = "6.19.0"
    known_keys = KNOWN_KEYS

    @staticmethod
    def path(g: GameConfig) -> str:
        return os.path.join(g.tool_dir, "config", "gui.new.json")

    def file_paths(self, g):
        return [self.path(g)]

    def config_names(self, g):
        d = read_json(self.path(g)) or {}
        return list((d.get("Configurations") or {}).keys())

    def default_config(self, g):
        name = str(g.options.get("config", "")).strip()
        names = self.config_names(g)
        if name in names:
            return name
        cur = (read_json(self.path(g)) or {}).get("Current")
        return cur if cur in names else (names[0] if names else "")

    def busy(self, g):
        return adapters.get(g.adapter).running_instances(g)

    def _materials(self, g: GameConfig) -> list:
        items = read_json(os.path.join(g.tool_dir, "resource", "item_index.json")) or {}
        mats = sorted(((v.get("sortId", 0), k, v.get("name", k)) for k, v in items.items()
                       if isinstance(v, dict) and v.get("classifyType") == "MATERIAL"))
        return [("", "（不指定）")] + [(k, name) for _s, k, name in mats]

    def load(self, g, config_name):
        if not os.path.isfile(self.path(g)):
            raise SettingsError(f"找不到 {self.path(g)}")
        f = JsonFile(self.path(g))
        conf = (f.data.get("Configurations") or {}).get(config_name)
        if not isinstance(conf, dict):
            raise SettingsError(f"MAA 里没有配置「{config_name}」")
        model = Model(files=[f], sections=[])
        gui = conf.get("Gui")
        if isinstance(gui, dict):
            sec = Section("gui", "启动与收尾", gui, [x for x in GUI_FIELDS if has_path(gui, x.key)],
                          subtitle=f"配置「{config_name}」本身的设置")
            model.sections.append(sec)
            missing = [x.label for x in GUI_FIELDS if not has_path(gui, x.key)]
            if missing:       # .Gui also holds connection / UI settings the page does not show: only check ours
                model.drift.append(f"启动与收尾：这些选项已不存在 {'、'.join(missing)}")
        queue = conf.get("TaskQueue")
        if isinstance(queue, list):
            model.order = queue
            mats = None
            for i, task in enumerate(queue):
                if not isinstance(task, dict):
                    continue
                typ = str(task.get("$type", ""))
                table = fields = TYPE_FIELDS.get(typ, [])
                if typ == "FightTask":
                    mats = mats if mats is not None else self._materials(g)
                    fields = [Field(**{**x.__dict__, "choices": mats}) if x.key == "DropId" else x for x in fields]
                fields = [x for x in fields if x.key.split(".")[0] in task]   # never add keys MAA does not have
                note = "" if typ in TYPE_FIELDS else "这类任务的详细选项请在 MAA 里设置，这里只能开关和调顺序。"
                if typ not in TYPE_NAMES:
                    model.drift.append(f"任务队列里有总控不认识的任务类型 {typ}（只能开关和调顺序）")
                sec = Section(f"task{i}", str(task.get("Name") or TYPE_NAMES.get(typ, typ)), task, fields,
                              enable_key="IsEnable" if "IsEnable" in task else None, movable=True,
                              subtitle=TYPE_NAMES.get(typ, typ), note=note)
                model.sections.append(sec)
                if typ in TYPE_FIELDS:
                    self.audit(model, sec, typ, table)
        if not f.exact:
            model.notes.append("这个文件的格式无法原样写回，只能查看，请在 MAA 里修改。")
        model.notes.append("改动在下次启动 MAA 时生效。")
        return model
