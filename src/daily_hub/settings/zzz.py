"""绝区零 · zzz-onedragon daily settings: config/<NN>/one_dragon/_group.yml + one_dragon/<app_id>.yml.

Evidence: docs/research/settings/zzz.json (checked against git HEAD bb44c00d) and the files themselves.
- The one-dragon app list is _group.yml app_list: [{app_id, enabled}], run in list order
  (application_group_config.py:48-74).  Each app's options are a flat mapping in one_dragon/<app_id>.yml
  (application_config.py:20-23); a key missing from the file means the getter's default
  (`return self.get('<key>', <default>)` in <app>_config.py), so most files hold only a few keys.
- Files are PyYAML yaml.dump output (UTF-8, CRLF, mapping 2 / sequence 2 / offset 0); ruamel round-trips
  every one of them byte for byte, edits change only the edited lines (new keys are appended).
  The tool reads with PyYAML (YAML 1.1): new strings that 1.1 reads as bool/int/... are written quoted.
- No schema file: option keys, defaults and enum choices are read from the tool's source with ast at load
  time (never imported), so new enum values appear; keys the source stops reading become read-only.
  Labels follow the tool's GUI (gui/app_setting/*, charge_plan_interface.py, one_dragon_run_interface.py).
- Options files that do not exist yet (8 apps run on defaults) are shown read-only: creating them is out of
  scope, and the tool migrates an old config/<NN>/<app_id>.yml only when the new file is missing.
- The tool writes these files while it runs (charge_plan.yml after every fight) and the GUI saves every
  change at once -> read-only while any one-dragon process runs (the adapter's running_instances).
- Dangerous values the adapter warns about (恢复电量 other than 不使用) are never offered here; turning
  them off is.  Turning on 驱动盘拆解 (irreversible) is left to the tool.
- game_account.yml / telemetry.yml / push.yml are never opened.
"""
from __future__ import annotations

import ast
import dataclasses
import glob
import os
import re

from .. import adapters
from ..adapters.zzz_onedragon import git_head, git_refs
from ..config import GameConfig
from .base import Field, Model, Section, SettingsError, SettingsProvider, YamlFile, set_path

F = Field
_APP_SRC = ("src", "zzz_od", "application")
GROUP_TITLE = "一条龙（按顺序执行）"
PLAN_GROUP = "体力计划（体力刷本的计划表）"

# app_id -> (config source under src/zzz_od/application, config class)
CONFIG_SRC = {
    "random_play": ("random_play/random_play_config.py", "RandomPlayConfig"),
    "charge_plan": ("charge_plan/charge_plan_config.py", "ChargePlanConfig"),
    "coffee": ("coffee/coffee_config.py", "CoffeeConfig"),
    "notorious_hunt": ("notorious_hunt/notorious_hunt_config.py", "NotoriousHuntConfig"),
    "lost_void": ("hollow_zero/lost_void/lost_void_config.py", "LostVoidConfig"),
    "suibian_temple": ("suibian_temple/suibian_temple_config.py", "SuibianTempleConfig"),
    "drive_disc_dismantle": ("drive_disc_dismantle/drive_disc_dismantle_config.py", "DriveDiscDismantleConfig"),
    "life_on_line": ("life_on_line/life_on_line_config.py", "LifeOnLineConfig"),
    "world_patrol": ("world_patrol/world_patrol_config.py", "WorldPatrolConfig"),
    "withered_domain": ("hollow_zero/withered_domain/withered_domain_config.py", "WitheredDomainConfig"),
    "intel_board": ("intel_board/intel_board_config.py", "IntelBoardConfig"),
    "daily_signin": ("daily_signin/daily_signin_config.py", "DailySignInConfig"),
}
PLAN_SRC = ("charge_plan/charge_plan_config.py", "ChargePlanItem")
_SUIBIAN_OPS = "suibian_temple/operations/suibian_temple_adventure_dispatch.py"

TEAM_HELP = "预备编队的名字取自一条龙的编队设置（config/<实例>/team.yml）。"
AUTO_BATTLE_HELP = "只在预备编队选“游戏内配队”时使用（一条龙界面也只在这时显示）。"

APP_FIELDS: dict[str, list[Field]] = {
    "random_play": [
        F("transport_point", "传送地点", "choice"),
        F("agent_name_1", "影像店代理人-1", "choice"),
        F("agent_name_2", "影像店代理人-2", "choice"),
    ],
    "charge_plan": [
        F("loop", "循环执行", risk="开着时每次运行都会把自然电量刷光",
          help="开启后，全部计划均达到计划次数后，已运行次数会清零并开始下一轮（一条龙原文）"),
        F("skip_plan", "跳过计划", help="开启后，某项计划因电量不足且无法恢复达标时，会依次尝试后续计划（一条龙原文）"),
        F("daily_reset_plan_times", "每日重置",
          help="开启后，每天首次运行体力计划前，会将所有体力计划已运行次数清零（一条龙原文）"),
        F("restore_charge", "恢复电量", "choice", risk="选“不使用”以外的值会消耗储蓄电量和/或以太电池",
          help="电量不足时尝试按所选方式有效补足缺口（一条龙原文）。\n"
               "总控这里只提供“不使用”；要用储蓄电量或以太电池，请在一条龙里设置。"),
        F("double_reward", "启用双倍活动", risk="有双倍活动时会额外进实战模拟室，消耗电量",
          help="有活动时自动添加对应任务（一条龙原文）"),
    ],
    "coffee": [
        F("transport_point", "传送地点", "choice"),
        F("choose_way", "咖啡选择", "choice"),
        F("challenge_way", "喝后挑战", "choice",
          risk="选“不挑战”以外的值，喝咖啡后会进副本消耗电量；恢复电量不是“不使用”时还可能动用储蓄电量或以太电池"),
        F("card_num", "体力计划外的数量", "choice", risk="“默认数量”按游戏内设的卡片数刷，消耗电量更多"),
        F("predefined_team_idx", "预备编队", "choice", help=TEAM_HELP),
        F("auto_battle", "自动战斗", "choice", help=AUTO_BATTLE_HELP),
        F("run_charge_plan_afterwards", "结束后运行体力计划", risk="开启后会再刷一遍体力计划，消耗电量",
          help="咖啡店在体力计划后运行可开启（一条龙原文）"),
    ],
    "notorious_hunt": [
        F("weekly_challenge_start_weekday", "恶名狩猎（周期挑战）开始日", "choice",
          help="从开始日起才会自动运行。当日未完成会顺延到次日，直到当周完成/结束（一条龙原文）"),
        F("loop", "循环执行"),
    ],
    "lost_void": [
        F("mission_name", "挑战副本", "text", readonly=True, help="选项随游戏变化，请在一条龙里改。"),
        F("extra_task", "刷取目标", "choice"),
        F("weekly_plan_times", "每周进入次数", "int", max=99, help="刷取目标选“完成周计划次数”时使用"),
        F("daily_plan_times", "每天进入次数", "int", max=99, help="分摊到每天运行（一条龙原文）"),
        F("challenge_config", "挑战配置", "text", readonly=True,
          help="选择角色、鸣徽和事件（一条龙原文）。配置内容在 config/lost_void_challenge，请在一条龙里改。"),
    ],
    "suibian_temple": [
        F("auto_manage_enabled", "自动托管",
          help="启用后选择游戏内自动托管经营随便观相关任务，随便观35级后可用（一条龙原文）。开启时一条龙不显示饮茶仙的两项。"),
        F("yum_cha_sin", "饮茶仙", risk="会提交采办材料，必要时自动制造或游历补料"),
        F("yum_cha_sin_period_refresh", "饮茶仙-委托刷新", enabled_by="yum_cha_sin"),
        F("adventure_duration", "派遣-时长", "choice"),
        F("adventure_mission_1", "派遣-副本优先级 1", "choice", help="按优先级将剩余小队派遣（一条龙原文）"),
        F("adventure_mission_2", "派遣-副本优先级 2", "choice"),
        F("adventure_mission_3", "派遣-副本优先级 3", "choice"),
        F("adventure_mission_4", "派遣-副本优先级 4", "choice"),
        F("craft_drag_times", "制造坊-最大下拉次数", "int", max=99, help="跳过底部的低级商品（一条龙原文）"),
        F("good_goods_purchase_enabled", "好物铺购买", risk="花货币购买商品",
          help="自动购买好物铺中的指定商品（一条龙原文）"),
        F("boo_box_purchase_enabled", "邦巢-购买", risk="刷新并购买邦布，花货币",
          help="自动刷新购买S级别邦布。随便观25级后可用。（一条龙原文）"),
        F("boo_box_adventure_price", "邦巢-最低购买价格：游历", "choice", enabled_by="boo_box_purchase_enabled",
          risk="价格越高花得越多"),
        F("boo_box_craft_price", "邦巢-最低购买价格：制造", "choice", enabled_by="boo_box_purchase_enabled",
          risk="价格越高花得越多"),
        F("boo_box_sell_price", "邦巢-最低购买价格：售卖", "choice", enabled_by="boo_box_purchase_enabled",
          risk="价格越高花得越多"),
        F("pawnshop_omnicoin_enabled", "德丰大押-百宝通", risk="用百通宝兑换商品",
          help="一条龙界面不显示这一项（标签取自配置说明）。"),
        F("pawnshop_crest_enabled", "德丰大押-云纹徽", risk="用云纹徽兑换商品",
          help="一条龙界面不显示这一项（标签取自配置说明）。"),
        F("pawnshop_crest_unlimited_denny_enabled", "德丰大押-云纹徽-不限购丁尼", enabled_by="pawnshop_crest_enabled",
          risk="把剩余云纹徽不限量换成丁尼"),
    ],
    "drive_disc_dismantle": [
        F("dismantle_level", "拆解等级", "choice", risk="分解驱动盘，不可逆；选“S及以下”时连 S 级也会分解"),
        F("dismantle_abandon", "全部已弃置", risk="分解所有标记为弃置的驱动盘，不可逆"),
    ],
    "life_on_line": [
        F("daily_plan_times", "每日次数", "int", max=20000),
        F("predefined_team_idx", "预备编队", "choice", help=TEAM_HELP),
    ],
    "world_patrol": [
        F("route_list", "路线名单", "text", readonly=True, help="空 = 全部路线。路线名单请在一条龙里选。"),
        F("auto_battle", "自动战斗", "choice"),
        F("daily_loop_count", "锄地每日循环次数", "int", max=99, help="一次任务执行内总共执行的轮数（一条龙原文）"),
        F("loop_interval_seconds", "每轮最少占用时长（敌人刷新时间）", "int", max=86400, suffix=" 秒"),
        F("ui_disappear_action", "界面消失处理方式", "choice",
          [("silent_fail", "静默失败"), ("restart_and_skip", "重开游戏并跳过路线"),
           ("restart_and_retry", "重开游戏并重试路线")]),
        F("route_retry_action", "路线重试处理方式", "choice",
          [("skip_on_stuck_again", "若再次卡住则跳过脱困"), ("retry_on_stuck_again", "若再次卡住仍尝试脱困")]),
    ],
    "withered_domain": [
        F("weekly_plan_times", "每周基础次数", "int", max=99, help="完整通关，用于完成委托任务（一条龙原文）"),
        F("daily_plan_times", "每天进入次数", "int", max=99, help="将空洞分摊到每天运行（一条龙原文）"),
        F("extra_task", "额外刷取", "choice"),
        F("extra_exit", "额外刷取方式", "choice"),
    ],
    "intel_board": [
        F("predefined_team_idx", "预备编队", "choice", help=TEAM_HELP),
        F("auto_battle_config", "自动战斗", "choice", help=AUTO_BATTLE_HELP),
        F("exp_grind_mode", "刷满经验", help="开启后按经验值判断完成，而不是按情报板进度（配置说明）"),
    ],
    "daily_signin": [
        F("selected_sign", "选择签到商店", "choice"),
    ],
}

PLAN_FIELDS = [
    F("category_name", "副本分类", "text", readonly=True),
    F("mission_type_name", "副本", "text", readonly=True),
    F("mission_name", "关卡", "text", readonly=True),
    F("card_num", "卡片数量", "choice", risk="张数越多，一次消耗的电量越多（20×张数）"),
    F("notorious_hunt_buff_num", "BUFF", "choice"),
    F("predefined_team_idx", "预备编队", "choice", help=TEAM_HELP),
    F("auto_battle_config", "自动战斗", "choice", help=AUTO_BATTLE_HELP),
    F("run_times", "已运行次数", "int", max=999_999, help="一条龙每打完一次 +1；改小会让这个计划重刷、多消耗电量。"),
    F("plan_times", "计划次数", "int", max=999_999, risk="按计划消耗电量"),
]

# Where choices come from: ("enum", source, Enum class) parsed at load, or a dynamic list.
TEAM, AUTO_BATTLE, AGENT, SIGNIN = "team", "auto_battle", "agent", "signin"
_CP = "charge_plan/charge_plan_config.py"
_NH = "notorious_hunt/notorious_hunt_config.py"
_SB = "suibian_temple/suibian_temple_config.py"
CHOICES = {
    ("random_play", "transport_point"): ("random_play/random_play_config.py", "RandomPlayTransportPoint"),
    ("random_play", "agent_name_1"): AGENT,
    ("random_play", "agent_name_2"): AGENT,
    ("charge_plan", "restore_charge"): (_CP, "RestoreChargeEnum"),
    ("coffee", "transport_point"): ("coffee/coffee_config.py", "CoffeeTransportPoint"),
    ("coffee", "choose_way"): ("coffee/coffee_config.py", "CoffeeChooseWay"),
    ("coffee", "challenge_way"): ("coffee/coffee_config.py", "CoffeeChallengeWay"),
    ("coffee", "card_num"): ("coffee/coffee_config.py", "CoffeeCardNumEnum"),
    ("coffee", "predefined_team_idx"): TEAM,
    ("coffee", "auto_battle"): AUTO_BATTLE,
    ("notorious_hunt", "weekly_challenge_start_weekday"): (_NH, "NotoriousHuntWeekdayEnum"),
    ("lost_void", "extra_task"): ("hollow_zero/lost_void/lost_void_config.py", "LostVoidTaskEnum"),
    ("suibian_temple", "adventure_duration"): (_SUIBIAN_OPS, "SuibianTempleAdventureDispatchDuration"),
    ("suibian_temple", "adventure_mission_1"): (_SB, "SuibianTempleAdventureMission"),
    ("suibian_temple", "adventure_mission_2"): (_SB, "SuibianTempleAdventureMission"),
    ("suibian_temple", "adventure_mission_3"): (_SB, "SuibianTempleAdventureMission"),
    ("suibian_temple", "adventure_mission_4"): (_SB, "SuibianTempleAdventureMission"),
    ("suibian_temple", "boo_box_adventure_price"): (_SB, "BangbooPrice"),
    ("suibian_temple", "boo_box_craft_price"): (_SB, "BangbooPrice"),
    ("suibian_temple", "boo_box_sell_price"): (_SB, "BangbooPrice"),
    ("drive_disc_dismantle", "dismantle_level"):
        ("drive_disc_dismantle/drive_disc_dismantle_config.py", "DismantleLevelEnum"),
    ("life_on_line", "predefined_team_idx"): TEAM,
    ("world_patrol", "auto_battle"): AUTO_BATTLE,
    ("withered_domain", "extra_task"): ("hollow_zero/withered_domain/withered_domain_config.py", "HollowZeroExtraTask"),
    ("withered_domain", "extra_exit"):
        ("hollow_zero/withered_domain/withered_domain_config.py", "HollowZeroExtraExitEnum"),
    ("intel_board", "predefined_team_idx"): TEAM,
    ("intel_board", "auto_battle_config"): AUTO_BATTLE,
    ("daily_signin", "selected_sign"): SIGNIN,
    ("plan", "card_num"): (_CP, "CardNumEnum"),
    ("plan", "notorious_hunt_buff_num"): (_NH, "NotoriousHuntBuffEnum"),
    ("plan", "predefined_team_idx"): TEAM,
    ("plan", "auto_battle_config"): AUTO_BATTLE,
}
# Values the adapter warns about are never offered: (app, key) -> the only value offered (an enum member name)
SAFE_ONLY = {("charge_plan", "restore_charge"): "NONE"}
SIGNIN_APPS = ["hou_hou_bakery", "trigrams_collection", "scratch_card"]   # daily_signin_setting_flyout.py:33-37

APP_NOTES = {
    "redemption_code": "兑换码清单在 config/redemption_codes.yml，请在一条龙里编辑。",
    "random_play": "无消耗，产出菲林与丁尼（一条龙注释）。",
    "charge_plan": "每次运行一条龙都会重跑这一项，按下面的体力计划消耗电量。计划表在左侧“体力计划”组。",
    "coffee": "每日免费点单 1 杯（一条龙注释）。",
    "notorious_hunt": "每周 3 次免费奖励，不耗电量（一条龙注释）。挑战计划请在一条龙里改。",
    "engagement_reward": "日刷新、无消耗（一条龙注释）。",
    "lost_void": "周限玩法，耗时长。",
    "shiyu_defense": "配队弱点表请在一条龙里改。",
    "suibian_temple": "消耗玩法自有资源，如开物秘卷、云纹徽、丁尼（一条龙注释）。",
    "city_fund": "无消耗（一条龙注释）。",
    "ridu_weekly": "周限、无体力消耗（一条龙注释）。",
    "email": "无消耗（一条龙注释）。",
    "drive_disc_dismantle": "⚠ 销毁道具，不可逆（一条龙注释）。这一项的开关只能在一条龙里打开或关闭。",
    "life_on_line": "可重复挑战的 HDD 战斗委托，主要用于推进游戏时间（一条龙注释）。",
    "notify": "发送运行结果通知；推送渠道请在一条龙里设置。",
    "world_patrol": "无体力消耗，耗时较长（一条龙注释）。",
    "withered_domain": "周限玩法，与迷失之地共享每周奖励次数（一条龙注释）。挑战配置请在一条龙里改。",
    "intel_board": "代行他人委托，不耗自身电量（一条龙注释）。",
    "daily_signin": "代为运行所选的签到应用，无消耗（一条龙注释）。",
}
LOCKED_SWITCH = {"drive_disc_dismantle"}

# Keys each config class's getters read at bb44c00d (from the source, not the files: the files store only
# changed values).  Keys read later are listed as new options; table keys no longer read become read-only.
KNOWN_KEYS = {
    'charge_plan': frozenset({
        'combat_simulation_double_reward_config', 'daily_reset_plan_times', 'double_reward',
        'last_daily_reset_dt', 'loop', 'restore_charge', 'skip_plan',
    }),
    'coffee': frozenset({
        'auto_battle', 'card_num', 'challenge_way', 'choose_way', 'day_coffee_1', 'day_coffee_2',
        'day_coffee_3', 'day_coffee_4', 'day_coffee_5', 'day_coffee_6', 'day_coffee_7',
        'predefined_team_idx', 'run_charge_plan_afterwards', 'transport_point',
    }),
    'daily_signin': frozenset({'selected_sign'}),
    'drive_disc_dismantle': frozenset({'dismantle_abandon', 'dismantle_level'}),
    'intel_board': frozenset({'auto_battle_config', 'exp_grind_mode', 'predefined_team_idx'}),
    'life_on_line': frozenset({'daily_plan_times', 'predefined_team_idx'}),
    'lost_void': frozenset({
        'challenge_config', 'daily_plan_times', 'extra_task', 'mission_name', 'weekly_plan_times',
    }),
    'notorious_hunt': frozenset({'loop', 'weekly_challenge_start_weekday'}),
    'plan': frozenset({
        'auto_battle_config', 'card_num', 'category_name', 'level', 'mission_name', 'mission_type_name',
        'notorious_hunt_buff_num', 'plan_id', 'plan_times', 'predefined_team_idx', 'run_times', 'skipped',
        'tab_name',
    }),
    'random_play': frozenset({'agent_name_1', 'agent_name_2', 'transport_point'}),
    'suibian_temple': frozenset({
        'adventure_duration', 'adventure_mission_1', 'adventure_mission_2', 'adventure_mission_3',
        'adventure_mission_4', 'auto_manage_enabled', 'boo_box_adventure_price', 'boo_box_craft_price',
        'boo_box_purchase_enabled', 'boo_box_sell_price', 'craft_drag_times', 'good_goods_purchase_enabled',
        'pawnshop_crest_enabled', 'pawnshop_crest_priority', 'pawnshop_crest_unlimited_denny_enabled',
        'pawnshop_omnicoin_enabled', 'pawnshop_omnicoin_priority', 'yum_cha_sin', 'yum_cha_sin_period_refresh',
    }),
    'withered_domain': frozenset({
        'challenge_config', 'daily_plan_times', 'extra_exit', 'extra_task', 'mission_name', 'weekly_plan_times',
    }),
    'world_patrol': frozenset({
        'auto_battle', 'daily_loop_count', 'loop_interval_seconds', 'route_list', 'route_retry_action',
        'route_retry_times', 'ui_disappear_action', 'ui_disappear_seconds',
    }),
}

_MISSING = object()
# Plain strings PyYAML (YAML 1.1) would read back as bool / int / float / null / date: written quoted.
_YAML11_SPECIAL = re.compile(
    r"^(?:|~|y|Y|yes|Yes|YES|n|N|no|No|NO|on|On|ON|off|Off|OFF|true|True|TRUE|false|False|FALSE|null|Null|NULL"
    r"|[-+]?(?:0b[01_]+|0x[0-9a-fA-F_]+|0[0-7_]+|[0-9][0-9_]*(?::[0-5]?[0-9])*)"
    r"|[-+]?(?:[0-9][0-9_]*)?\.[0-9_]*(?:[eE][-+]?[0-9]+)?|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN)"
    r"|\d{4}-\d\d?-\d\d?(?:[Tt ].*)?)$")


def _yaml11_safe(value):
    if type(value) is str and _YAML11_SPECIAL.match(value):
        from ruamel.yaml.scalarstring import SingleQuotedScalarString
        return SingleQuotedScalarString(value)
    return value


def _on_set(obj, fd: Field, value) -> None:
    set_path(obj, fd.key, _yaml11_safe(value))


# ---------------------------------------------------------------- source parsing (ast only, never imported)
@dataclasses.dataclass
class _Member:
    name: str
    stored: object        # what the config file holds
    label: str
    desc: str = ""
    str_enum: bool = False


class _Source:
    """Enum classes, constants and getter defaults of the tool's source files (parsed once per load)."""

    def __init__(self, app_dir: str):
        self.app_dir = app_dir
        self.trees: dict[str, ast.Module | None] = {}
        self.problems: list[str] = []

    def tree(self, rel: str) -> ast.Module | None:
        if rel not in self.trees:
            try:
                with open(os.path.join(self.app_dir, rel), encoding="utf-8") as fh:
                    self.trees[rel] = ast.parse(fh.read())
            except (OSError, SyntaxError, ValueError) as e:
                self.trees[rel] = None
                self.problems.append(f"读不了一条龙源码 {rel}：{type(e).__name__}")
        return self.trees[rel]

    def _class(self, rel: str, name: str) -> ast.ClassDef | None:
        t = self.tree(rel)
        return next((c for c in (t.body if t else []) if isinstance(c, ast.ClassDef) and c.name == name), None)

    def enum(self, rel: str, name: str) -> list[_Member] | None:
        c = self._class(rel, name)
        if c is None:
            return None
        str_enum = any("StrEnum" in ast.unparse(b) for b in c.bases)
        out = []
        for st in c.body:
            if not (isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name)):
                continue
            mname, v = st.targets[0].id, st.value
            try:
                if str_enum and isinstance(v, ast.Constant) and isinstance(v.value, str):
                    out.append(_Member(mname, mname, v.value, str_enum=True))
                elif isinstance(v, ast.Call) and ast.unparse(v.func) == "ConfigItem":
                    args = [ast.literal_eval(a) for a in v.args]
                    kw = {k.arg: ast.literal_eval(k.value) for k in v.keywords}
                    label = kw.get("label", args[0] if args else "")
                    value = kw.get("value", args[1] if len(args) > 1 else None)
                    desc = kw.get("desc", args[2] if len(args) > 2 else "") or ""
                    out.append(_Member(mname, label if value is None else value, str(label), str(desc)))
                elif isinstance(v, ast.Call) and ast.unparse(v.func) == "TransportPoint":
                    args = [ast.literal_eval(a) for a in v.args]
                    kw = {k.arg: ast.literal_eval(k.value) for k in v.keywords}
                    area, tp = kw.get("area_name", args[0] if args else ""), kw.get("tp_name", args[1] if len(args) > 1 else "")
                    out.append(_Member(mname, f"{area} - {tp}", f"{area} - {tp}"))
            except (ValueError, IndexError, TypeError, SyntaxError):
                continue
        return out

    def _resolve(self, rel: str, expr: ast.expr):
        try:
            return ast.literal_eval(expr)
        except (ValueError, TypeError, SyntaxError):
            pass
        parts = ast.unparse(expr).split(".")
        t = self.tree(rel)
        if t is None:
            return _MISSING
        if len(parts) == 1:                                   # module constant (RANDOM_AGENT_NAME = '随机')
            for st in t.body:
                if isinstance(st, ast.Assign) and any(isinstance(x, ast.Name) and x.id == parts[0] for x in st.targets):
                    try:
                        return ast.literal_eval(st.value)
                    except (ValueError, TypeError, SyntaxError):
                        return _MISSING
            return _MISSING
        # Enum.MEMBER.value.value / Enum.MEMBER.name / Class.CONSTANT (enum may live in an imported module)
        for src in [rel] + [r for r in self.trees if r != rel]:
            members = self.enum(src, parts[0]) if self._class(src, parts[0]) else None
            if members:
                m = next((x for x in members if x.name == parts[1]), None)
                if m is None:
                    return _MISSING
                rest = parts[2:]
                if rest == ["name"]:
                    return m.name
                if rest == ["value"] and m.str_enum:
                    return m.label
                if rest == ["value", "value"] and not m.str_enum:
                    return m.stored
                return _MISSING
            c = self._class(src, parts[0])
            if c is not None and len(parts) == 2:
                for st in c.body:
                    if isinstance(st, ast.Assign) and any(isinstance(x, ast.Name) and x.id == parts[1]
                                                          for x in st.targets):
                        try:
                            return ast.literal_eval(st.value)
                        except (ValueError, TypeError, SyntaxError):
                            return _MISSING
        return _MISSING

    def getters(self, rel: str, cls: str) -> dict[str, object] | None:
        """{key: default} from `return self.get('<key>', <default>)` in the class's @property getters."""
        c = self._class(rel, cls)
        if c is None:
            return None
        out: dict[str, object] = {}
        for fn in c.body:
            if not (isinstance(fn, ast.FunctionDef) and any(ast.unparse(d) == "property" for d in fn.decorator_list)):
                continue
            for n in ast.walk(fn):
                if (isinstance(n, ast.Call) and ast.unparse(n.func) == "self.get" and n.args
                        and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
                    out[n.args[0].value] = self._resolve(rel, n.args[1]) if len(n.args) > 1 else None
        return out

    def dataclass_fields(self, rel: str, cls: str) -> dict[str, object] | None:
        c = self._class(rel, cls)
        if c is None:
            return None
        return {st.target.id: (self._resolve(rel, st.value) if st.value is not None else _MISSING)
                for st in c.body if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name)}


def _app_names(app_dir: str) -> dict[str, tuple[str, bool]]:
    """{APP_ID: (APP_NAME, DEFAULT_GROUP)} from src/zzz_od/application/**/*_const.py."""
    out = {}
    for f in glob.glob(os.path.join(app_dir, "**", "*_const.py"), recursive=True):
        try:
            with open(f, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        aid = re.search(r"""^APP_ID\s*(?::\s*str\s*)?=\s*['"]([^'"]+)['"]""", text, re.M)
        name = re.search(r"""^APP_NAME\s*(?::\s*str\s*)?=\s*['"]([^'"]+)['"]""", text, re.M)
        group = re.search(r"^DEFAULT_GROUP\s*(?::\s*bool\s*)?=\s*(True|False)", text, re.M)
        if aid:
            out[aid.group(1)] = (name.group(1) if name else aid.group(1), bool(group and group.group(1) == "True"))
    return out


def _safe_yaml(path: str):
    from ruamel.yaml import YAML
    try:
        with open(path, encoding="utf-8") as fh:
            return YAML(typ="safe", pure=True).load(fh)
    except Exception:          # noqa: BLE001 - an unreadable helper file only loses its choices
        return None


class _AppView(dict):
    """Section.obj of one app: 'enabled' lives in the _group.yml item, the options in one_dragon/<app_id>.yml.

    A dict subclass so the shared helpers (get_path / set_path / has_path) work; it stores nothing itself."""

    def __init__(self, item: dict, opts: dict):
        super().__init__()
        self.item, self.opts = item, opts

    def _src(self, key):
        return self.item if key == "enabled" else self.opts

    def __getitem__(self, key):
        return self._src(key)[key]

    def __setitem__(self, key, value):
        self._src(key)[key] = value

    def __contains__(self, key):
        return key in self._src(key)

    def get(self, key, default=None):
        return self._src(key).get(key, default)

    def setdefault(self, key, default=None):
        return self._src(key).setdefault(key, default)

    def keys(self):
        return self.opts.keys()

    def __iter__(self):
        return iter(self.opts)

    def __len__(self):
        return len(self.opts)


class _Order:
    """Model.order over _group.yml app_list: yields the sections' _AppView objects, swaps the real items."""

    def __init__(self, app_list: list, views: dict[int, _AppView]):
        self.app_list, self.views = app_list, views

    def __iter__(self):
        return (self.views.get(id(x)) for x in self.app_list)

    def __len__(self):
        return len(self.app_list)

    def __getitem__(self, i):
        return self.views.get(id(self.app_list[i]))

    def __setitem__(self, i, view):
        self.app_list[i] = view.item


class ZzzSettings(SettingsProvider):
    key = "zzz_onedragon"
    verified_version = "bb44c00d"
    known_keys = KNOWN_KEYS

    def __init__(self):
        self._src_keys: dict[str, set] = {}

    # ---- paths
    @staticmethod
    def _app_dir(g: GameConfig) -> str:
        return os.path.join(g.tool_dir, *_APP_SRC)

    @staticmethod
    def _inst_dirs(g: GameConfig) -> list[str]:
        return sorted(os.path.basename(p) for p in glob.glob(os.path.join(g.tool_dir, "config", "[0-9][0-9]"))
                      if os.path.isdir(p))

    def file_paths(self, g):
        out = []
        for nn in self._inst_dirs(g):
            od = os.path.join(g.tool_dir, "config", nn, "one_dragon")
            out.append(os.path.join(od, "_group.yml"))
            out += [os.path.join(od, f"{a}.yml") for a in APP_FIELDS] + [os.path.join(od, "charge_plan.yml")]
        return list(dict.fromkeys(out))

    def config_names(self, g):
        data = _safe_yaml(os.path.join(g.tool_dir, "config", "one_dragon.yml")) or {}
        names = []
        for x in data.get("instance_list") or []:
            if isinstance(x, dict) and isinstance(x.get("idx"), int):
                nn, label = f"{x['idx']:02d}", str(x.get("name") or "")
                names.append(nn if label in ("", nn) else f"{nn} {label}")
        return names or self._inst_dirs(g)

    def default_config(self, g):
        names = self.config_names(g)
        inst = str(g.options.get("instance", "1")).split(",")[0].strip()
        want = f"{int(inst):02d}" if inst.isdigit() else inst
        return next((n for n in names if n.split(" ")[0] == want), names[0] if names else "")

    def busy(self, g):
        return adapters.get(g.adapter).running_instances(g)

    # ---- dynamic choices
    @staticmethod
    def _teams(g: GameConfig, cfg_dir: str) -> list:
        data = _safe_yaml(os.path.join(cfg_dir, "team.yml")) or {}
        names = [str(t.get("name") or "") for t in (data.get("team_list") or []) if isinstance(t, dict)]
        names += [""] * max(0, 20 - len(names))
        return [(-1, "游戏内配队")] + [(i, n or f"编队{i + 1}") for i, n in enumerate(names)]

    @staticmethod
    def _auto_battle(g: GameConfig) -> list:
        names = set()
        for p in glob.glob(os.path.join(g.tool_dir, "config", "auto_battle", "*.yml")):
            n = os.path.basename(p)
            for suf in (".sample.yml", ".merged.yml", ".yml"):
                if n.endswith(suf):
                    names.add(n[: -len(suf)])
                    break
        return [(n, n) for n in sorted(names, key=str.lower)]

    @staticmethod
    def _agents(src: _Source) -> list:
        out = [("随机", "随机")]
        c = src._class("../game_data/agent.py", "AgentEnum")
        for st in (c.body if c else []):
            if isinstance(st, ast.Assign) and isinstance(st.value, ast.Call) and len(st.value.args) > 1:
                try:
                    name = ast.literal_eval(st.value.args[1])
                except (ValueError, TypeError, SyntaxError):
                    continue
                if isinstance(name, str) and (name, name) not in out:
                    out.append((name, name))
        return out

    def _choices(self, spec, ctx: dict, model: Model, title: str, fd: Field, safe: str | None):
        """(choices, extra help) for one field; None when the source no longer has the enum."""
        if spec == TEAM:
            return ctx["teams"], ""
        if spec == AUTO_BATTLE:
            return ctx["auto_battle"], ""
        if spec == AGENT:
            ctx.setdefault("agents", self._agents(ctx["src"]))
            return ctx["agents"], ""
        if spec == SIGNIN:
            names = ctx["apps"]
            return [(a, names.get(a, (a,))[0]) for a in SIGNIN_APPS], ""
        rel, enum_name = spec
        members = ctx["src"].enum(rel, enum_name)
        if not members:
            model.drift.append(f"{title}：一条龙源码里找不到选项表 {enum_name}，「{fd.label}」只读")
            return None
        if safe:
            members = [m for m in members if m.name == safe]
        desc = "\n".join(f"{m.label}：{m.desc}" for m in members if m.desc)
        return [(m.stored, m.label) for m in members], desc

    def _fields(self, kind: str, table: list[Field], ctx: dict, model: Model, title: str,
                defaults: dict | None, readonly: bool) -> list[Field]:
        out = []
        for fd in table:
            changes: dict = {}
            spec = CHOICES.get((kind, fd.key))
            if spec is not None:
                got = self._choices(spec, ctx, model, title, fd, SAFE_ONLY.get((kind, fd.key)))
                if got is None:
                    changes["readonly"] = True
                else:
                    changes["choices"] = got[0]
                    if got[1]:
                        changes["help"] = "\n".join(x for x in (fd.help, got[1]) if x)
            if defaults is not None:
                d = defaults.get(fd.key, _MISSING)
                if d is not _MISSING:
                    changes["default"] = d
            if readonly:
                changes["readonly"] = True
            out.append(dataclasses.replace(fd, **changes))
        return out

    def audit(self, model, section, kind, table, ignore=frozenset()):
        """Source-based: compare the keys the tool's getters read now with KNOWN_KEYS.

        (base.audit compares file keys, but these files store only changed values: a missing key is normal.)"""
        known, cur = self.known_keys.get(kind), self._src_keys.get(kind)
        if known is None or cur is None:
            return
        new = sorted(cur - known - ignore)
        gone = sorted({fd.root for fd in table} - cur)
        if new:
            section.new_keys = new
            model.drift.append(f"{section.title}：一条龙新增了选项 {'、'.join(new)}（总控还没有说明，只读显示）")
        if gone:
            for fd in section.fields:
                if fd.root in gone:
                    fd.readonly = True
            model.drift.append(f"{section.title}：一条龙已不再读取 {'、'.join(gone)}（只读）")

    # ---- load
    def load(self, g, config_name):
        nn = (config_name or "").split(" ")[0]
        cfg_dir = os.path.join(g.tool_dir, "config", nn)
        od = os.path.join(cfg_dir, "one_dragon")
        group_path = os.path.join(od, "_group.yml")
        if not nn or not os.path.isfile(group_path):
            raise SettingsError(f"找不到 {group_path}")
        app_dir = self._app_dir(g)
        if not os.path.isdir(app_dir):
            raise SettingsError(f"找不到一条龙源码目录 {app_dir}")
        group = YamlFile(group_path)
        app_list = group.data.get("app_list") if isinstance(group.data, dict) else None
        if not isinstance(app_list, list):
            raise SettingsError(f"{group_path} 里没有 app_list")
        model = Model(files=[group], sections=[])
        src = _Source(app_dir)
        apps = _app_names(app_dir)
        ctx = {"src": src, "apps": apps, "teams": self._teams(g, cfg_dir), "auto_battle": self._auto_battle(g)}
        self._src_keys = {}
        for spec in CHOICES.values():          # enums imported by the config modules resolve their defaults
            if isinstance(spec, tuple):
                src.tree(spec[0])
        for app_id, (rel, cls) in CONFIG_SRC.items():
            got = src.getters(rel, cls)
            if got is None:
                model.drift.append(f"一条龙源码里找不到 {app_id} 的设置类 {cls}（这一项的选项只读）")
            else:
                self._src_keys[app_id] = set(got)

        self._global_section(g, model)
        views: dict[int, _AppView] = {}
        opt_files: dict[str, YamlFile] = {}
        seen = set()
        for item in app_list:
            if not isinstance(item, dict) or "app_id" not in item:
                continue
            app_id = str(item["app_id"])
            seen.add(app_id)
            name = apps.get(app_id, (app_id,))[0]
            notes = [APP_NOTES.get(app_id, "")]
            if app_id not in apps:
                model.drift.append(f"一条龙列表里的应用 {app_id} 在源码里已不存在（保留不动）")
            opts, ro = {}, False
            table = APP_FIELDS.get(app_id, [])
            if table:
                path = os.path.join(od, f"{app_id}.yml")
                old = os.path.join(cfg_dir, f"{app_id}.yml")
                if os.path.isfile(path):
                    f = YamlFile(path)
                    if f.exact and isinstance(f.data, dict):
                        opts = f.data
                        opt_files[app_id] = f
                    else:
                        opts, ro = (f.data if isinstance(f.data, dict) else {}), True
                        notes.append(f"{app_id}.yml 不能原样写回（{f.reason or '格式不对'}），选项只读，请在一条龙里改。")
                elif os.path.isfile(old):
                    opts, ro = _safe_yaml(old) or {}, True
                    notes.append(f"选项还在旧文件 config/{nn}/{app_id}.yml 里，一条龙下次用到这一项时会搬过来；"
                                 "在那之前只读。")
                else:
                    ro = True
                    notes.append("这一项还没有选项文件，一条龙一直用默认值（下面显示的就是默认值）。"
                                 "要改请先在一条龙里改一次，之后就可以在这里改。")
            defaults = None
            if app_id in CONFIG_SRC:
                rel, cls = CONFIG_SRC[app_id]
                defaults = src.getters(rel, cls)
                ro = ro or defaults is None
            fields = self._fields(app_id, table, ctx, model, name, defaults, ro)
            if app_id in LOCKED_SWITCH:
                fields.insert(0, F("enabled", "启用", readonly=True, risk="启用后每次运行都会拆解驱动盘（不可逆）"))
            view = _AppView(item, opts)
            views[id(item)] = view
            sec = Section(f"app.{app_id}", name, view, fields,
                          enable_key=None if app_id in LOCKED_SWITCH else "enabled", movable=True,
                          note="\n".join(x for x in notes if x), group=GROUP_TITLE, on_set=_on_set)
            model.sections.append(sec)
            if table:
                self.audit(model, sec, app_id, table)
        model.order = _Order(app_list, views)
        for app_id, (name, default_group) in sorted(apps.items()):
            if default_group and app_id not in seen:
                model.drift.append(f"一条龙新增了应用「{name}」，还不在列表里（先在一条龙里打开一次这一项）")

        self._plan_sections(g, model, ctx, opt_files.get("charge_plan"))
        model.files += list(opt_files.values())
        if src.problems:
            model.drift += src.problems
        if not group.exact:
            model.notes.append(f"_group.yml 不能原样写回（{group.reason}），整页只读，请在一条龙里修改。")
        model.notes.append("勾选 = 一条龙运行这一项，列表顺序就是执行顺序。改动在下次运行一条龙时生效。")
        model.notes.append("体力计划只能改次数、卡片数和编队；新增、删除、调整计划顺序、更换副本请在一条龙里做。")
        refs = git_refs(g.tool_dir)
        head, remote = git_head(g.tool_dir), refs.get("refs/remotes/origin/main")
        if head and remote and head != remote:
            model.notes.append(f"一条龙已下载新版本（{remote[:8]}）但还没安装；在一条龙里更新代码后，这页会按新版本核对。")
        return model

    def _global_section(self, g: GameConfig, model: Model) -> None:
        data = _safe_yaml(os.path.join(g.tool_dir, "config", "one_dragon.yml"))
        if not isinstance(data, dict):
            return
        fd = F("instance_run", "运行实例", "choice", [("全部实例", "全部实例"), ("仅运行当前", "仅运行当前")],
               readonly=True, default="全部实例",
               help="总控用 --instance 指定实例；设成“仅运行当前”时这个参数不生效。请在一条龙里改。")
        model.sections.append(Section("od_instance", "运行实例", data, [fd], group="全局",
                                      note="一条龙的全局设置（config/one_dragon.yml），这里只读。"))
        if data.get("instance_run") == "仅运行当前":
            model.notes.append("一条龙的“运行实例”是“仅运行当前”：总控传的 --instance 不会生效。")

    def _plan_sections(self, g: GameConfig, model: Model, ctx: dict, f: YamlFile | None) -> None:
        if f is None or not isinstance(f.data.get("plan_list"), list):
            return
        rel, cls = PLAN_SRC
        defaults = ctx["src"].dataclass_fields(rel, cls)
        gone: set = set()
        if defaults is None:
            model.drift.append(f"一条龙源码里找不到 {cls}，体力计划只读")
        else:          # same check as audit(), reported once for the whole list instead of per plan
            new = sorted(set(defaults) - KNOWN_KEYS["plan"])
            gone = {fd.root for fd in PLAN_FIELDS} - set(defaults)
            if new:
                model.drift.append(f"体力计划：一条龙新增了字段 {'、'.join(new)}（总控不显示，原样保留）")
            if gone:
                model.drift.append(f"体力计划：一条龙已不再使用 {'、'.join(sorted(gone))}（只读）")
        disp = self._compendium(g)
        for i, item in enumerate(f.data["plan_list"]):
            if not isinstance(item, dict):
                continue
            cat = str(item.get("category_name") or "")
            mtype, mission = item.get("mission_type_name"), item.get("mission_name")
            table = [fd for fd in PLAN_FIELDS
                     if not (fd.key == "card_num" and cat != "实战模拟室")
                     and not (fd.key == "notorious_hunt_buff_num" and cat != "恶名狩猎")
                     and not (fd.key in ("predefined_team_idx", "auto_battle_config") and cat == "合成电池")
                     and not (fd.key == "mission_name" and cat != "实战模拟室")]
            fields = self._fields("plan", table, ctx, model, f"计划 {i + 1}",
                                  {k: v for k, v in (defaults or {}).items() if v is not _MISSING},
                                  defaults is None)
            for fd in fields:
                fd.readonly = fd.readonly or fd.root in gone
            shown = [cat, disp.get((cat, mtype), mtype or "")]
            if cat == "实战模拟室":
                shown.append(disp.get((cat, mtype, mission), mission or ""))
            sec = Section(f"plan{i}", f"计划 {i + 1}：{' · '.join(x for x in shown if x)}", item, fields,
                          subtitle=f"{item.get('run_times')}/{item.get('plan_times')}", group=PLAN_GROUP,
                          on_set=_on_set)
            model.sections.append(sec)

    @staticmethod
    def _compendium(g: GameConfig) -> dict:
        """Display names of the training missions ({(category, type): name, (category, type, mission): name})."""
        data = _safe_yaml(os.path.join(g.tool_dir, "assets", "game_data", "compendium_data.yml")) or []
        out = {}
        for tab in data if isinstance(data, list) else []:
            for cat in (tab or {}).get("category_list") or []:
                cn = cat.get("category_name")
                for mt in cat.get("mission_type_list") or []:
                    tn = mt.get("mission_type_name")
                    if mt.get("mission_type_name_display"):
                        out[(cn, tn)] = mt["mission_type_name_display"]
                    for m in mt.get("mission_list") or []:
                        if m.get("mission_name_display"):
                            out[(cn, tn, m.get("mission_name"))] = m["mission_name_display"]
        return out
