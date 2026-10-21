"""BetterGI (原神) daily settings: User/OneDragon/<Name>.json plus the parts of User/config.json a
one-dragon run reads.

Evidence: docs/research/settings/genshin.json (BetterGI 0.66.0: BetterGI.dll metadata / IL and the page
BAML, so labels are BetterGI's own zh-CN strings) and the files themselves.
- Both files round-trip byte for byte through JsonFile (UTF-8 without BOM, CRLF, 2-space indent, no
  trailing newline): <Name>.json is written by Newtonsoft.Json, config.json by System.Text.Json.
- BetterGI picks a one-dragon config by its JSON "Name" and always writes it to <Name>.json: a file whose
  stem differs from its Name, or a Name two files share, opens read-only; Name is never edited.
- Task list: TaskOrder [id] is the run order (one loop, built-ins and 配置组 interleaved), TaskEnabledList
  {id: bool} the switch, TaskDefinitions {id: name} the name.  The name picks the action (8 built-ins,
  any other name runs User/ScriptGroup/<name>.json).  Ids missing from TaskEnabledList (or from a
  non-empty TaskDefinitions) are dropped by BetterGI, so they are not shown.  Per-task options are flat
  root keys: copies of a task share them.  TaskOrder holds ids, not objects -> _FlowModel.move swaps ids.
- SecretTreasureObjects is one list [day, item, ...] ([] = buy nothing): edited as two virtual fields.
- config.json is read strictly (a type mismatch makes BetterGI start from defaults and later overwrite
  every setting): values keep their JSON type (maxArtifactStar / *SelectedValue strings, bossNum int).
  It also holds secrets (notificationConfig, device ids, cookies): only the listed keys are shown.
- BetterGI rewrites both files on any change in its window, at run start and on exit -> read-only while
  BetterGI.exe / BetterGI.update.exe / its instance pipe exists.
- Settings the run adapter refuses (CompletionAction 关机, NextTaskId, autoRestartConfig) and its non-resource
  warnings (exitToTray, autoUpdateBeforeCommandLineRun) are read-only or not offered; resin / salvage
  options are editable and marked as spending resources.
"""
from __future__ import annotations

import glob
import os
import re
from dataclasses import replace

from .. import adapters
from ..adapters.base import read_json
from ..config import GameConfig
from .base import Field, JsonFile, Model, Section, SettingsError, SettingsProvider, get_path, has_path

F = Field


def _same(values: list) -> list:
    return [(v, v) for v in values]


BUILTINS = ("领取邮件", "合成树脂", "自动秘境", "自动首领讨伐", "自动幽境危战", "自动地脉花", "领取每日奖励", "领取尘歌壶奖励")
DAYS = [("Monday", "周一"), ("Tuesday", "周二"), ("Wednesday", "周三"), ("Thursday", "周四"),
        ("Friday", "周五"), ("Saturday", "周六"), ("Sunday", "周日")]
COUNTRIES = ["蒙德", "璃月", "稻妻", "须弥", "枫丹", "纳塔", "挪德卡莱", "至冬"]
LEY_TYPES = ["启示之花", "藏金之花"]
REWARD_NO = [("0", "不指定"), ("1", "1"), ("2", "2"), ("3", "3")]
GUIDE = "根据提升指南选择秘境"
AUTO_STRATEGY = "根据队伍自动选择"
COMBO_STRATEGY = "自动连招（实验）"
POT_DAYS = ["每天重复", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
POT_ITEMS = ["布匹", "须臾树脂", "大英雄的经验", "流浪者的经验", "精锻用魔矿", "摩拉", "祝圣精华", "祝圣油膏"]
POT_DAY, POT_BUY = "SecretTreasureObjects[0]", "SecretTreasureObjects[1:]"     # virtual keys (one list)
GUARD = "；总控运行时发现用了须臾/脆弱树脂会中止本次运行"
RESIN_GROUP = "一条龙 · 任务（勾选 = 启用，可调顺序）"
GLOBAL_GROUP = "全局设置（User/config.json，所有一条龙配置与独立任务共用）"

FLOW_FIELDS = [
    F("CompletionAction", "完成后操作", "choice", _same(["无", "关闭软件", "关闭游戏", "关闭游戏和软件"]),
      help="任务完成后执行的操作（一条龙结束后操作）。“关闭游戏”会关掉本会话里所有原神进程。总控推荐“关闭软件”。\n"
           "“关机”不在这里提供：电脑的睡眠、关机请用总控运行页的“完成后”（总控预检会拦截“关机”）。"),
    F("NextTaskId", "从此执行（续跑标记）", "text", readonly=True,
      help="BetterGI 界面右键“从此执行”设的标记；不为空时一条龙从该任务开始，跳过前面的任务，开跑时会被清空。"
           "总控预检会拦截非空值，请在 BetterGI 里右键“清除标记”。"),
]

CRAFT_FIELDS = [
    F("CraftingBenchCountry", "合成树脂合成台", "choice", _same(["枫丹", "稻妻", "璃月", "蒙德"]), help="指定地区合成树脂"),
    F("MinResinToKeep", "合成后保留原粹树脂数量", "int", max=200, risk="合成会消耗原粹树脂和合成材料",
      help="合成浓缩树脂后至少留下这么多原粹树脂；0 = 能合的都合掉。"),
]

DOMAIN_FIELDS = [
    F("PartyName", "进入秘境切换的队伍名称", "text", help="注意是游戏内你设置的名称；留空则不切换队伍。"),
    F("DomainName", "选择秘境", "choice", help="秘境名称（列表取自 BetterGI 的 GameTask/AutoTrackPath/Assets/tp.json）。"),
    F("SundayEverySelectedValue", "周日或限时 选择序号", "choice", REWARD_NO,
      help="周日或限时全开秘境时，选第几个奖励（天赋书/武器材料）。0 = 不指定。"),
    F("WeeklyDomainEnabled", "每周秘境刷取配置",
      help="启用后，每日刷取配置将会失效。周期始于凌晨 4:00 ，如周一 4:00 至周二 3:59 刷取周一秘境。\n"
           "某天的队伍、秘境留空时用上面的队伍名称、选择秘境。"),
    F("SundayWeeklySelectedValue", "默认 · 奖励", "choice", REWARD_NO, enabled_by="WeeklyDomainEnabled",
      help="每周配置的默认奖励序号；某天的序号为空或 0 时用它。"),
] + [fd for day, zh in DAYS for fd in (
    F(f"{day}PartyName", f"{zh} · 队伍", "text", enabled_by="WeeklyDomainEnabled",
      help="留空使用默认队伍" if day == "Monday" else ""),
    F(f"{day}DomainName", f"{zh} · 秘境", "choice", enabled_by="WeeklyDomainEnabled",
      help="留空使用默认秘境" if day == "Monday" else ""),
    F(f"{day}SelectedValue", f"{zh} · 奖励", "choice", REWARD_NO, enabled_by="WeeklyDomainEnabled",
      help="为空或 0 时用默认奖励序号" if day == "Monday" else ""),
)]

BOSS_FIELDS = [
    F("AutoBossStrategyName", "选择战斗策略", "choice", help="仅用于首领讨伐，不覆盖其他策略设置。"),
    F("AutoBossName", "选择首领", "choice",
      help="部分首领因机制问题未添加。列表取自 BetterGI 的首领路线文件（GameTask/AutoBoss/Assets/Pathing），"
           "可能与 BetterGI 的下拉框略有出入，找不到的请在 BetterGI 里选。留空时该任务记错误并跳过。"),
    F("AutoBossTeamName", "切换队伍", "text", help="留空则不更换队伍（例如：首领队）"),
    F("AutoBossSpecifyRunCount", "成功领奖次数限制", risk="关闭时会刷到原粹树脂耗尽",
      help="开启后，可分别限制单次运行和跨运行累计的成功领奖次数。关闭时刷取至原粹树脂耗尽。"),
    F("AutoBossRunCount", "单次运行上限", "int", min=1, max=999, suffix=" 次", risk="消耗原粹树脂",
      enabled_by="AutoBossSpecifyRunCount"),
    F("AutoBossTotalRunCountLimit", "累计运行上限", "int", max=99999, suffix=" 次", enabled_by="AutoBossSpecifyRunCount",
      help="累计上限为 0 时不限制；达到上限后该任务跳过。"),
    F("AutoBossCompletedRunCount", "当前已累计成功领奖（次）", "int", readonly=True,
      help="BetterGI 每次成功领奖自动加 1。"),
    F("AutoBossUseTransientResin", "原粹不足时使用须臾树脂补充", risk="消耗须臾树脂（补充道具）" + GUARD),
    F("AutoBossUseFragileResin", "原粹不足时使用脆弱树脂补充", risk="消耗脆弱树脂（稀有补充道具）" + GUARD),
    F("AutoBossReturnToStatueAfterEachRound", "每轮讨伐后返回七天神像", help="开启后每次领奖后先回血，再重新前往首领"),
    F("AutoBossRewardRecognitionEnabled", "启用奖励识别", help="每轮领取后识别奖励名称与数量，任务结束打印汇总"),
    F("AutoBossReviveRetryCount", "角色死亡后重试次数", "int", max=99, help="战斗中存在角色死亡时，复活后重新讨伐当前首领"),
    F("AutoBossTimeout", "自动战斗超时", "int", min=1, max=3600, suffix=" 秒", help="到达指定时间后，自动停止战斗。"),
]

LEYLINE_FIELDS = [
    F("LeyLineOneDragonMode", "跳过准备流程", help="跳过部分准备流程（例如传送回七天神像）。"),
    F("LeyLineResinExhaustionMode", "树脂耗尽模式", risk="会把树脂刷完", help="按当前树脂与库存自动计算可刷次数，结束后自动停止。"),
    F("LeyLineOpenModeCountMin", "刷取次数取小值", help="与手动次数取最小值，避免超过树脂可用次数。"),
    F("LeyLineRunCount", "刷取次数", "int", max=999, risk="消耗树脂", help="填 0 则使用独立任务配置（下方“自动地脉花（独立任务默认值）”）。"),
] + [F(f"LeyLineRun{day}", f"运行日期：{zh}",
       help="仅在勾选的星期执行；若未勾选任何星期，则每天执行。（按服务器时间 4:00 换日）" if day == "Monday" else "")
     for day, zh in DAYS
] + [fd for day, zh in DAYS for fd in (
    F(f"LeyLine{day}Type", f"{zh} · 类型", "choice", [("", "（使用独立任务默认）")] + _same(LEY_TYPES),
      help="类型/国家留空时使用独立任务默认设置。" if day == "Monday" else ""),
    F(f"LeyLine{day}Country", f"{zh} · 国家", "choice", [("", "（使用独立任务默认）")] + _same(COUNTRIES)),
)]

DAILY_FIELDS = [
    F("AdventurersGuildCountry", "领取奖励的冒险者协会", "choice", _same(["挪德卡莱", "枫丹", "稻妻", "璃月", "蒙德"]),
      help="前往指定地区冒险者协会领取"),
    F("DailyRewardPartyName", "领取前切换队伍（好感队）", "text", help="用于给指定队伍加好感度；留空不切换。"),
]

POT_FIELDS = [
    F("SereniteaPotTpType", "进壶方式选择", "choice", _same(["地图传送", "尘歌壶道具"])),
    F(POT_DAY, "购买日期", "choice", [("", "（不购买）")] + _same(POT_DAYS), risk="用洞天宝钱在尘歌壶商店购物",
      help="与下一项组成同一个列表；至少勾选一样商品才会购买。日期不影响领取好感和钱币。"),
    F(POT_BUY, "购买选择", "multi", _same(POT_ITEMS), enabled_by=POT_DAY, risk="花洞天宝钱",
      help="勾选后在阿圆处用洞天宝钱购买。"),
]

TASK_FIELDS = {"合成树脂": CRAFT_FIELDS, "自动秘境": DOMAIN_FIELDS, "自动首领讨伐": BOSS_FIELDS,
               "自动地脉花": LEYLINE_FIELDS, "领取每日奖励": DAILY_FIELDS, "领取尘歌壶奖励": POT_FIELDS}

TASK_NOTES = {
    "领取邮件": "没有可调选项。",
    "合成树脂": "⚠ 把原粹树脂合成为浓缩树脂（每个 40 原粹，另按游戏配方耗合成材料）。"
               "BetterGI 读取的是 config.json 里选中的一条龙配置（见页面顶部说明）。",
    "自动秘境": "⚠ 消耗树脂。用哪种树脂、刷几次、是否分解圣遗物在下方“自动秘境 · 领奖树脂与圣遗物”里"
               "（config.json，所有一条龙配置和独立任务共用）。",
    "自动首领讨伐": "⚠ 每次领奖消耗原粹树脂；关闭“成功领奖次数限制”时刷到原粹树脂耗尽。"
                 "一条龙只用这里的设置，不读 config.json 的 autoBossConfig。",
    "自动幽境危战": "⚠ 消耗树脂（耗尽模式下先用浓缩）。选项全部在下方“自动幽境危战”（config.json，与独立任务共用）。",
    "自动地脉花": "⚠ 消耗原粹/浓缩树脂。具体参数在下方“自动地脉花（独立任务默认值）”。"
               "运行时 BetterGI 会临时改写 config.json 的地脉花类型/国家/次数/耗尽模式，结束后再改回。",
    "领取每日奖励": "去冒险家协会领每日委托奖励，之后领取纪行奖励。",
    "领取尘歌壶奖励": "BetterGI 读取的是 config.json 里选中的一条龙配置（见页面顶部说明）。",
}

GLOBAL_DOMAIN = [
    F("autoDomainConfig.specifyResinUse", "指定每种树脂刷取次数", risk="关闭（耗尽模式）会先用光全部浓缩树脂，再用原粹",
      help="关闭 = 刷取至树脂耗尽：优先使用浓缩树脂，然后使用原粹树脂，其余树脂不使用。\n"
           "开启 = 按下方次数使用树脂，顺序：浓缩 → 原粹(40) → 原粹(20) → 原粹 → 须臾 → 脆弱；"
           "开启但全部为 0 时任务报错跳过。"),
    F("autoDomainConfig.originalResinUseCount", "原粹树脂刷取次数", "int", max=999, risk="消耗原粹树脂",
      enabled_by="autoDomainConfig.specifyResinUse"),
    F("autoDomainConfig.condensedResinUseCount", "浓缩树脂刷取次数", "int", max=999, risk="消耗浓缩树脂",
      enabled_by="autoDomainConfig.specifyResinUse"),
    F("autoDomainConfig.transientResinUseCount", "须臾树脂刷取次数", "int", max=999,
      risk="消耗须臾树脂（补充道具）" + GUARD, enabled_by="autoDomainConfig.specifyResinUse"),
    F("autoDomainConfig.fragileResinUseCount", "脆弱树脂刷取次数", "int", max=999,
      risk="消耗脆弱树脂（稀有补充道具）" + GUARD, enabled_by="autoDomainConfig.specifyResinUse"),
    F("autoDomainConfig.originalResin40UseCount", "原粹树脂(40)刷取次数", "int", readonly=True, risk="消耗原粹树脂",
      help="BetterGI 界面上没有这一项，但指定模式下会先于“原粹树脂刷取次数”使用。"),
    F("autoDomainConfig.originalResin20UseCount", "原粹树脂(20)刷取次数", "int", readonly=True, risk="消耗原粹树脂",
      help="BetterGI 界面上没有这一项，但指定模式下会使用。"),
    F("autoDomainConfig.autoArtifactSalvage", "结束后自动分解圣遗物", risk="不可逆分解圣遗物"),
    F("autoArtifactSalvageConfig.maxArtifactStar", "需要快速分解圣遗物的最高星级", "choice", _same(["4", "3", "2", "1"]),
      risk="分解该星级及以下的圣遗物", help="该星级及以下的圣遗物会被快速分解。自动幽境危战也用这一项。"),
    F("autoDomainConfig.rewardRecognitionEnabled", "启用奖励识别",
      help="每轮领取后识别奖励名称与数量（图标匹配+OCR双路），任务结束打印汇总"),
    F("autoDomainConfig.autoEat", "自动吃药", risk="消耗料理", help="请先装备“便携营养袋”，在红血时候后自动按Z键吃药"),
    F("autoDomainConfig.reviveRetryCount", "角色死亡后重试次数", "int", max=99, help="秘境战斗中，发生角色死亡重试的次数"),
    F("autoFightConfig.strategyName", "选择战斗策略（自动秘境）", "choice",
      help="用于战斗。策略文件不存在时自动秘境记错误并跳过。"),
]

GLOBAL_STYGIAN = [
    F("autoStygianOnslaughtConfig.strategyName", "选择战斗策略", "choice", help="为空时 BetterGI 改成“根据队伍自动选择”。"),
    F("autoStygianOnslaughtConfig.bossNum", "指定刷取的战场", "choice", [(1, "1"), (2, "2"), (3, "3")],
      help="从上到下战场一、二、三"),
    F("autoStygianOnslaughtConfig.fightTeamName", "指定战斗队伍", "text", help="输入预设队伍的名称，留空则不更换队伍"),
    F("autoStygianOnslaughtConfig.specifyResinUse", "指定每种树脂刷取次数", risk="关闭会先用光浓缩树脂",
      help="关闭 = 刷取至树脂耗尽：优先使用浓缩树脂，然后使用原粹树脂，其余树脂不使用。\n"
           "开启 = 按下方次数，顺序 浓缩 → 原粹 → 须臾 → 脆弱。"),
    F("autoStygianOnslaughtConfig.originalResinUseCount", "原粹树脂刷取次数", "int", max=999, risk="消耗原粹树脂",
      enabled_by="autoStygianOnslaughtConfig.specifyResinUse"),
    F("autoStygianOnslaughtConfig.condensedResinUseCount", "浓缩树脂刷取次数", "int", max=999, risk="消耗浓缩树脂",
      enabled_by="autoStygianOnslaughtConfig.specifyResinUse"),
    F("autoStygianOnslaughtConfig.transientResinUseCount", "须臾树脂刷取次数", "int", max=999,
      risk="消耗须臾树脂（补充道具）" + GUARD, enabled_by="autoStygianOnslaughtConfig.specifyResinUse"),
    F("autoStygianOnslaughtConfig.fragileResinUseCount", "脆弱树脂刷取次数", "int", max=999,
      risk="消耗脆弱树脂（稀有补充道具）" + GUARD, enabled_by="autoStygianOnslaughtConfig.specifyResinUse"),
    F("autoStygianOnslaughtConfig.autoArtifactSalvage", "结束后自动分解圣遗物", risk="不可逆分解圣遗物",
      help="分解星级用“需要快速分解圣遗物的最高星级”（在“自动秘境 · 领奖树脂与圣遗物”里，与自动秘境共用）。"),
]

GLOBAL_LEYLINE = [
    F("autoLeyLineOutcropConfig.leyLineOutcropType", "地脉花类型", "choice", _same(LEY_TYPES),
      help="选择刷取的地脉花，启示之花（经验书）或藏金之花（摩拉）。"),
    F("autoLeyLineOutcropConfig.country", "国家", "choice", _same(COUNTRIES), help="按国家选择刷取对应的地脉花。"),
    F("autoLeyLineOutcropConfig.count", "刷取次数", "int", min=1, max=999, risk="消耗树脂",
      help="树脂耗尽模式关闭或统计失败时使用的固定次数。"),
    F("autoLeyLineOutcropConfig.useTransientResin", "使用须臾树脂", risk="消耗须臾树脂（补充道具）" + GUARD,
      help="原粹与浓缩耗尽后，允许使用须臾树脂继续刷取。"),
    F("autoLeyLineOutcropConfig.useFragileResin", "使用脆弱树脂", risk="消耗脆弱树脂（稀有补充道具）" + GUARD,
      help="原粹与浓缩耗尽后，允许使用脆弱树脂继续刷取。"),
    F("autoLeyLineOutcropConfig.team", "战斗队伍名称", "text", help="进入战斗前切换到该队伍，留空则不切换。如果配置了好感队则必须填写。"),
    F("autoLeyLineOutcropConfig.friendshipTeam", "好感队名称", "text", help="领取奖励前切换到该队伍，留空则不切换。"),
    F("autoLeyLineOutcropConfig.fightConfig.strategyName", "选择战斗策略", "choice"),
]

GLOBAL_RUN = [
    F("genshinStartConfig.linkedStartEnabled", "同时启动原神", help="启动截图器时，如果原神未启动，则自动启动原神。（总控会自己先启动原神。）"),
    F("genshinStartConfig.installPath", "原神安装位置", "text",
      help="不支持云原神的联动启动。路径无效时 BetterGI 联动启动会弹窗后空等；总控默认自己启动原神。"),
    F("genshinStartConfig.autoEnterGameEnabled", "自动进入游戏", help="原神启动后自动进入游戏（自动开门、领取月卡）"),
    F("genshinStartConfig.autoDisableGenshinHdrEnabled", "启动前自动关闭原神 HDR", help="检测到开启时会自动关闭原神 HDR 注册表设置"),
    F("commonConfig.exitToTray", "退出时最小化到系统托盘", readonly=True,
      help="启用后点击右上角退出按钮会最小化到系统托盘继续运行。总控需要它关闭，否则只能强制结束 BetterGI；请在 BetterGI 里改。"),
    F("scriptConfig.autoUpdateBeforeCommandLineRun", "命令行启动时也自动更新", readonly=True,
      help="通过命令行参数启动配置组/一条龙时，先等待脚本更新完成再执行。总控预检会提示；请在 BetterGI 里改。"),
    F("scriptConfig.autoUpdateSubscribedScripts", "启动时自动更新已订阅的脚本", help="启动时自动同步脚本仓库并更新所有已订阅的脚本"),
    F("otherConfig.autoRestartConfig.enabled", "调度器异常重启配置", readonly=True,
      help="当调度器任务异常抛出未预期错误时，累计一定次数后，自动重启bgi。会打乱总控的进程跟踪，必须关闭"
           "（总控预检会拦截）；请在 BetterGI 里改。"),
]

# (id, title, fields, config.json sub-configs to audit, note)
GLOBAL_SECTIONS = [
    ("global:domain", "自动秘境 · 领奖树脂与圣遗物", GLOBAL_DOMAIN,
     ("autoDomainConfig", "autoArtifactSalvageConfig", "autoFightConfig"),
     "一条龙“自动秘境”的队伍、秘境用一条龙配置里的值；这里是用哪种树脂、刷几次和圣遗物分解。"),
    ("global:stygian", "自动幽境危战", GLOBAL_STYGIAN, ("autoStygianOnslaughtConfig",),
     "一条龙“自动幽境危战”只读这里。"),
    ("global:leyline", "自动地脉花（独立任务默认值）", GLOBAL_LEYLINE, ("autoLeyLineOutcropConfig",),
     "一条龙按星期设置留空时使用这里的类型/国家；一条龙“刷取次数”为 0 时使用这里的次数。"),
    ("global:run", "启动与无人值守", GLOBAL_RUN,
     ("genshinStartConfig", "commonConfig", "scriptConfig", "otherConfig.autoRestartConfig"),
     "这些影响总控无人值守运行；总控预检会检查其中几项。"),
]

# Keys at BetterGI 0.66.0: the one-dragon file root and the config.json sub-configs the page edits.
# New keys after an update are reported (one-dragon: listed read-only), not edited.
KNOWN_KEYS = {
    'OneDragon': frozenset({
        'AdventurersGuildCountry', 'AutoBossCompletedRunCount', 'AutoBossName',
        'AutoBossReturnToStatueAfterEachRound', 'AutoBossReviveRetryCount', 'AutoBossRewardRecognitionEnabled',
        'AutoBossRunCount', 'AutoBossSpecifyRunCount', 'AutoBossStrategyName', 'AutoBossTeamName',
        'AutoBossTimeout', 'AutoBossTotalRunCountLimit', 'AutoBossUseFragileResin', 'AutoBossUseTransientResin',
        'CompletionAction', 'CraftingBenchCountry', 'DailyRewardPartyName', 'DomainName', 'FridayDomainName',
        'FridayPartyName', 'FridaySelectedValue', 'LeyLineFridayCountry', 'LeyLineFridayType',
        'LeyLineMondayCountry', 'LeyLineMondayType', 'LeyLineOneDragonMode', 'LeyLineOpenModeCountMin',
        'LeyLineResinExhaustionMode', 'LeyLineRunCount', 'LeyLineRunFriday', 'LeyLineRunMonday',
        'LeyLineRunSaturday', 'LeyLineRunSunday', 'LeyLineRunThursday', 'LeyLineRunTuesday',
        'LeyLineRunWednesday', 'LeyLineSaturdayCountry', 'LeyLineSaturdayType', 'LeyLineSundayCountry',
        'LeyLineSundayType', 'LeyLineThursdayCountry', 'LeyLineThursdayType', 'LeyLineTuesdayCountry',
        'LeyLineTuesdayType', 'LeyLineWednesdayCountry', 'LeyLineWednesdayType', 'MinResinToKeep',
        'MondayDomainName', 'MondayPartyName', 'MondaySelectedValue', 'Name', 'NextTaskId', 'PartyName',
        'SaturdayDomainName', 'SaturdayPartyName', 'SaturdaySelectedValue', 'SecretTreasureObjects',
        'SereniteaPotTpType', 'SundayDomainName', 'SundayEverySelectedValue', 'SundayPartyName',
        'SundaySelectedValue', 'SundayWeeklySelectedValue', 'TaskDefinitions', 'TaskEnabledList', 'TaskOrder',
        'ThursdayDomainName', 'ThursdayPartyName', 'ThursdaySelectedValue', 'TuesdayDomainName',
        'TuesdayPartyName', 'TuesdaySelectedValue', 'WednesdayDomainName', 'WednesdayPartyName',
        'WednesdaySelectedValue', 'WeeklyDomainEnabled'
    }),
    'autoArtifactSalvageConfig': frozenset({
        'artifactSetFilter', 'javaScript', 'maxArtifactStar', 'maxNumToCheck', 'recognitionFailurePolicy',
        'regularExpression'
    }),
    'autoDomainConfig': frozenset({
        'autoArtifactSalvage', 'autoEat', 'condensedResinUseCount', 'domainName', 'fightEndDelay',
        'fragileResinUseCount', 'leftRightMoveTimes', 'originalResin20UseCount', 'originalResin40UseCount',
        'originalResinUseCount', 'partyName', 'resinPriorityList', 'reviveRetryCount',
        'rewardRecognitionEnabled', 'shortMovement', 'specifyResinUse', 'sundaySelectedValue',
        'transientResinUseCount', 'walkToF'
    }),
    'autoFightConfig': frozenset({
        'actionSchedulerByCd', 'battleThresholdForLoot', 'burstEnabled', 'damageNumberRecognitionMode',
        'drawRecognitionResults', 'enableCombatTargeting', 'expBasedPickupEnabled', 'fightFinishDetectEnabled',
        'finishDetectConfig', 'guardianAvatar', 'guardianAvatarHold', 'guardianCombatSkip', 'kazuhaPartyName',
        'kazuhaPickupEnabled', 'lockLostWaitTime', 'onlyPickEliteDropsMode', 'pickDropsAfterFightEnabled',
        'pickDropsAfterFightSeconds', 'qinDoublePickUp', 'strategyName', 'swimmingEnabled',
        'targetingDetectionInterval', 'teamNames', 'timeout'
    }),
    'autoLeyLineOutcropConfig': frozenset({
        'count', 'country', 'fightConfig', 'friendshipTeam', 'isGoToSynthesizer', 'isResinExhaustionMode',
        'leyLineOutcropType', 'openModeCountMin', 'scanDropsAfterRewardEnabled', 'scanDropsAfterRewardSeconds',
        'team', 'timeout', 'useFragileResin', 'useTransientResin'
    }),
    'autoStygianOnslaughtConfig': frozenset({
        'autoArtifactSalvage', 'bossNum', 'condensedResinUseCount', 'fightTeamName', 'fragileResinUseCount',
        'originalResinUseCount', 'resinPriorityList', 'specifyResinUse', 'strategyName',
        'transientResinUseCount'
    }),
    'commonConfig': frozenset({
        'currentBackdropType', 'currentThemeType', 'exitToTray', 'isFirstRun', 'mainBackgroundEnabled',
        'mainBackgroundImagePath', 'mainBackgroundOpacity', 'mainBackgroundStretch', 'onceHadRunDeviceIdList',
        'redeemCodeCnFeedsNotificationEnabled', 'redeemCodeFeedsUpdateVersion',
        'redeemCodeGlobalFeedsNotificationEnabled', 'redeemCodeGlobalFeedsUpdateVersion',
        'rewardRecognitionScreenshotEnabled', 'runForVersion', 'screenshotEnabled', 'screenshotUidCoverEnabled'
    }),
    'genshinStartConfig': frozenset({
        'autoDisableGenshinHdrEnabled', 'autoEnterGameEnabled', 'genshinStartArgs', 'installPath',
        'linkedStartEnabled', 'recordGameTimeEnabled', 'startGameWithCmd'
    }),
    'otherConfig.autoRestartConfig': frozenset({
        'enabled', 'failureCount', 'isFightFailureExceptional', 'isPathingFailureExceptional',
        'restartGameTogether'
    }),
    'scriptConfig': frozenset({
        'autoUpdateBeforeCommandLineRun', 'autoUpdateScriptRepoPeriod', 'autoUpdateSubscribedScripts',
        'customRepoUrl', 'guideStatus', 'lastUpdateScriptRepoTime', 'scriptRepoHintDotVisible',
        'selectedChannelName', 'subscribedScriptPaths', 'webviewHeight', 'webviewLeft', 'webviewState',
        'webviewTop', 'webviewWidth'
    }),
}


class _FlowModel(Model):
    """TaskOrder holds task ids (strings), so moving a task swaps ids instead of objects."""

    def move(self, index: int, delta: int) -> bool:
        j = index + delta
        if self.order is None or not (0 <= index < len(self.sections)) or not (0 <= j < len(self.sections)):
            return False
        a, b = self.sections[index], self.sections[j]
        if not (a.movable and b.movable):
            return False
        ia, ib = self.order.index(_task_id(a)), self.order.index(_task_id(b))
        self.order[ia], self.order[ib] = self.order[ib], self.order[ia]
        self.sections[index], self.sections[j] = b, a
        return True


def _task_id(s: Section) -> str:
    return s.id[len("task:"):]


def _copy(fields: list[Field], lists: dict) -> list[Field]:
    """Fresh Field objects (open() may mark them read-only) with the choice lists read at load time."""
    return [replace(fd, choices=lists[fd.key]) if fd.key in lists else replace(fd) for fd in fields]


class BetterGISettings(SettingsProvider):
    key = "bettergi"
    verified_version = "0.66.0"
    known_keys = KNOWN_KEYS

    @staticmethod
    def config_path(g: GameConfig) -> str:
        return os.path.join(g.tool_dir, "User", "config.json")

    @staticmethod
    def _flow_files(g: GameConfig) -> list[str]:
        return sorted(glob.glob(os.path.join(g.tool_dir, "User", "OneDragon", "*.json")))

    def file_paths(self, g):
        return self._flow_files(g) + [self.config_path(g)]

    def config_names(self, g):
        return adapters.get(g.adapter).config_names(g)

    def default_config(self, g):
        names = self.config_names(g)
        name = str(g.options.get("onedragon_config", "")).strip()
        if name in names:
            return name
        cur = (read_json(self.config_path(g)) or {}).get("selectedOneDragonFlowConfigName")
        return cur if cur in names else (names[0] if names else "")

    def busy(self, g):
        return adapters.get(g.adapter).running_instances(g)

    # ---- choice lists read from BetterGI's own files (they change with BetterGI updates) ----
    @staticmethod
    def _domains(g: GameConfig) -> list:
        data = read_json(os.path.join(g.tool_dir, "GameTask", "AutoTrackPath", "Assets", "tp.json")) or {}
        maps = data.get("data") if isinstance(data, dict) else None
        out = []
        for m in maps if isinstance(maps, list) else []:
            if isinstance(m, dict) and m.get("mapName") == "Teyvat":
                for p in m.get("points") or []:
                    if isinstance(p, dict) and p.get("type") in ("BlessDomain", "ForgeryDomain", "MasteryDomain") \
                            and p.get("name"):
                        country = p.get("country")
                        out.append((country if country in COUNTRIES else "", p["name"]))
        out.sort(key=lambda x: (COUNTRIES.index(x[0]) if x[0] in COUNTRIES else len(COUNTRIES)))
        return [(name, f"{c}·{name}" if c else name) for c, name in out]

    @staticmethod
    def _strategies(g: GameConfig) -> list[str]:
        base = os.path.join(g.tool_dir, "User", "AutoFight")
        names = []
        for folder, _dirs, files in os.walk(base):
            for f in files:
                stem, ext = os.path.splitext(f)
                if ext.lower() in (".txt", ".json"):
                    names.append(os.path.relpath(os.path.join(folder, stem), base))
        return sorted(names)

    @staticmethod
    def _bosses(g: GameConfig) -> list[str]:
        files = glob.glob(os.path.join(g.tool_dir, "GameTask", "AutoBoss", "Assets", "Pathing", "*.json"))
        stems = (os.path.splitext(os.path.basename(f))[0] for f in files)
        return sorted({re.sub(r"(战斗后快速前往|键鼠前往|强制传送|前往)$", "", s) for s in stems} - {""})

    def _choice_lists(self, g: GameConfig) -> dict:
        domains = self._domains(g)
        files = _same(self._strategies(g))
        strategies = [(AUTO_STRATEGY, AUTO_STRATEGY), (COMBO_STRATEGY, COMBO_STRATEGY)] + files
        lists = {
            "DomainName": [("", "（未选择：该任务会记错误并跳过）"), (GUIDE, GUIDE)] + domains,
            "AutoBossName": [("", "（未选择：该任务会记错误并跳过）")] + _same(self._bosses(g)),
            "AutoBossStrategyName": [(AUTO_STRATEGY, AUTO_STRATEGY)] + files,
            "autoFightConfig.strategyName": strategies,
            "autoStygianOnslaughtConfig.strategyName": strategies,
            "autoLeyLineOutcropConfig.fightConfig.strategyName": [("", "（未设置）")] + strategies,
        }
        for day, _zh in DAYS:
            lists[f"{day}DomainName"] = [("", "（使用默认秘境）"), (GUIDE, GUIDE)] + domains
        return lists

    # ---- loading ----
    def _flow_file(self, g: GameConfig, config_name: str) -> tuple[JsonFile, str]:
        matches = [p for p in self._flow_files(g) if (read_json(p) or {}).get("Name") == config_name]
        if not matches:
            raise SettingsError(f"BetterGI 里没有一条龙配置「{config_name}」")
        f = JsonFile(matches[0])
        why = ""
        if len(matches) > 1:
            why = f"有 {len(matches)} 个一条龙配置文件都叫「{config_name}」"
        elif os.path.splitext(os.path.basename(f.path))[0] != config_name:
            why = f"文件名 {os.path.basename(f.path)} 与配置名「{config_name}」不一致，BetterGI 保存时会另建文件"
        if why:
            f.exact, f.reason = False, why
        return f, why

    def load(self, g, config_name):
        if not os.path.isfile(self.config_path(g)):
            raise SettingsError(f"找不到 {self.config_path(g)}")
        od, why = self._flow_file(g, config_name)
        cfg = JsonFile(self.config_path(g))
        root, conf = od.data, cfg.data
        if not isinstance(root, dict) or not isinstance(conf, dict):
            raise SettingsError("BetterGI 的配置文件格式不对")
        model = _FlowModel(files=[od, cfg], sections=[])
        lists = self._choice_lists(g)

        flow = Section("flow", f"一条龙配置「{config_name}」", root,
                       [fd for fd in _copy(FLOW_FIELDS, lists) if has_path(root, fd.key)], group="一条龙",
                       note="BetterGI 按 JSON 里的 Name 选配置；名称、任务的增删和改名请在 BetterGI 里做。")
        model.sections.append(flow)
        table = [fd for fields in [FLOW_FIELDS, *TASK_FIELDS.values()] for fd in fields if fd.key not in (POT_DAY, POT_BUY)]
        self.audit(model, flow, "OneDragon", table + [F("SecretTreasureObjects", "")])

        self._add_tasks(g, model, root, lists)

        for sid, title, fields, subs, note in GLOBAL_SECTIONS:
            sec = Section(sid, title, conf, [fd for fd in _copy(fields, lists) if has_path(conf, fd.key)],
                          group=GLOBAL_GROUP, note=note)
            model.sections.append(sec)
            for sub in subs:
                self._audit_sub(model, sec, sub, fields)

        if why:
            model.notes.append(f"只能查看：{why}。请在 BetterGI 里处理。")
        elif not model.exact:
            model.notes.append("配置文件的格式无法原样写回，只能查看，请在 BetterGI 里修改。")
        selected = conf.get("selectedOneDragonFlowConfigName")
        if isinstance(selected, str) and selected != config_name:
            model.notes.append(f"BetterGI 当前选中的一条龙配置是「{selected}」：合成树脂的保留数量和尘歌壶购买读的是选中的配置，"
                               "总控用命令行启动这个配置后才会切换过来。")
        run_for = get_path(conf, "commonConfig.runForVersion")
        ver = self.version(g)
        if isinstance(run_for, str) and ver and run_for.strip() != ver.strip():
            model.notes.append(f"BetterGI 已更新到 {ver}，但还没启动过（配置记录的是 {run_for}）；"
                               "第一次启动时它可能会改写配置。")
        model.notes.append("自动秘境的树脂、圣遗物分解和幽境危战、地脉花默认值是全局设置，也会影响在 BetterGI 里手动运行的独立任务。")
        model.notes.append("改动在下次启动 BetterGI 时生效。")
        return model

    def _add_tasks(self, g: GameConfig, model: Model, root: dict, lists: dict) -> None:
        enabled, names, order = root.get("TaskEnabledList"), root.get("TaskDefinitions"), root.get("TaskOrder")
        if not isinstance(enabled, dict):
            model.drift.append("一条龙配置里没有 TaskEnabledList，任务列表请在 BetterGI 里查看")
            return
        names = names if isinstance(names, dict) else {}
        order_ok = isinstance(order, list) and bool(order) and all(isinstance(x, str) for x in order)
        ids = list(order) if order_ok else [str(k) for k in enabled]
        legacy = not names                              # old format: the id is the name
        movable = order_ok and not legacy and len(set(ids)) == len(ids)
        if movable:
            model.order = order
        shown = [i for i in ids if i in enabled and (legacy or i in names)]
        if len(shown) < len(ids):
            model.notes.append(f"一条龙的任务顺序里有 {len(ids) - len(shown)} 项缺少名称或开关，BetterGI 不会显示和执行，这里也不显示。")
        if not movable:
            model.notes.append("这个一条龙配置的任务顺序请在 BetterGI 里调整（文件是旧格式或顺序列表为空）。")
        counts = {}
        for i in shown:
            counts[str(names.get(i, i))] = counts.get(str(names.get(i, i)), 0) + 1
        pot_proxy = None
        for tid in shown:
            name = str(names.get(tid, tid))
            notes = []
            obj, on_set, fields = root, None, []
            if name in BUILTINS:
                subtitle = ""
                fields = _copy(TASK_FIELDS.get(name, []), lists)
                if name == "领取尘歌壶奖励":
                    if pot_proxy is None:
                        pot_proxy = self._pot_proxy(model, root)
                    obj, on_set = pot_proxy
                fields = [fd for fd in fields if has_path(obj, fd.key)]
                notes.append(TASK_NOTES.get(name, ""))
                if counts[name] > 1:
                    notes.append(f"列表里有 {counts[name]} 个「{name}」，它们共用同一组选项。")
            elif os.path.isfile(os.path.join(g.tool_dir, "User", "ScriptGroup", name + ".json")):
                subtitle = "配置组"
                notes.append("配置组：内容在 BetterGI 的调度器里改，这里只能开关和调顺序。和内置任务按列表顺序穿插执行。")
            else:
                subtitle = "未知任务"
                notes.append("不是 BetterGI 0.66.0 的内置任务，也找不到同名配置组（User/ScriptGroup），"
                             "这里只能开关和调顺序；运行时该项可能失败。")
                model.drift.append(f"一条龙任务「{name}」总控不认识（可能是 BetterGI 新增的任务）")
            model.sections.append(Section(
                f"task:{tid}", name, obj, fields, enable_key=("TaskEnabledList", tid), movable=movable,
                subtitle=subtitle, note="\n".join(n for n in notes if n), group=RESIN_GROUP, on_set=on_set))

    @staticmethod
    def _pot_proxy(model: Model, root: dict) -> tuple[dict, object]:
        """领取尘歌壶奖励 edits SecretTreasureObjects ([day, item, ...], [] = buy nothing) as two virtual
        fields.  The section edits a small dict; on_set writes the real keys back into the file data."""
        proxy = {"TaskEnabledList": root["TaskEnabledList"]}     # same dict: the task switch writes through
        if "SereniteaPotTpType" in root:
            proxy["SereniteaPotTpType"] = root["SereniteaPotTpType"]
        lst = root.get("SecretTreasureObjects")
        if isinstance(lst, list) and all(isinstance(x, str) for x in lst):
            proxy[POT_DAY] = lst[0] if lst else ""
            proxy[POT_BUY] = list(lst[1:])
        elif "SecretTreasureObjects" in root:
            model.drift.append("领取尘歌壶奖励：SecretTreasureObjects 的格式变了，购买设置请在 BetterGI 里改")

        def on_set(obj: dict, fd: Field, value) -> None:
            obj[fd.key] = value
            if fd.key in (POT_DAY, POT_BUY):
                day, items = obj.get(POT_DAY, ""), obj.get(POT_BUY, [])
                root["SecretTreasureObjects"] = [day, *items] if day and items else []
            else:
                root[fd.key] = value
        return proxy, on_set

    def _audit_sub(self, model: Model, sec: Section, sub: str, fields: list[Field]) -> None:
        """audit() for a config.json sub-config the section reaches with dotted keys (obj is the file root)."""
        obj = get_path(sec.obj, sub)
        if not isinstance(obj, dict):
            model.drift.append(f"{sec.title}：config.json 里已没有 {sub}")
            return
        known = self.known_keys.get(sub, frozenset())
        new = sorted(set(map(str, obj)) - known)
        used = {fd.key[len(sub) + 1:].split(".")[0] for fd in fields if fd.key.startswith(sub + ".")}
        gone = sorted(used - set(obj))
        if new:
            model.drift.append(f"{sec.title}：{sub} 出现了新选项 {'、'.join(new)}（总控不显示、不改动）")
        if gone:
            model.drift.append(f"{sec.title}：这些选项已不存在 {sub}.{('、' + sub + '.').join(gone)}")
