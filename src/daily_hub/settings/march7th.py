"""三月七小助手 (星穹铁道) daily settings: <tool_dir>/config.yaml.

Evidence: docs/research/settings/starrail.json (v2026.10.16, [A] = CLI PYZ, [L] = Launcher PYZ) and the files.
- config.yaml is one flat YAML map (347 top-level keys at v2026.10.16), UTF-8 without BOM, CRLF.  M7A
  writes it with ruamel.yaml 0.19.1 YAML() defaults (width 80, indent 2/2/0), the same version as the hub,
  and that layout reproduces it byte for byte.  The shared YamlFile forces width=4096, which would fold the
  two long notify_* lines differently, so this module opens it with the tool's own layout (_M7aYaml).
- Every M7A process (CLI, Launcher) rewrites the whole file on start and on each set_value, using
  assets/config/config.example.yaml as the skeleton: keys missing from the example are dropped, missing keys
  are added with defaults ([A]module/config/config.py:166, :178-185).  So the hub only edits existing keys,
  and a config.yaml whose keys differ from the installed example (updated but not started yet) is read-only.
- A file M7A cannot parse is moved to config.yaml.bak and replaced by defaults (config.py:256-309): the
  output is re-parsed before writing and must keep the same top-level keys.
- The run order is fixed in code (tasks/daily/daily.py), so no section is movable.
- Labels and help follow the Launcher's own strings ([L]app/setting_interface.py, app/card/*); the
  instance-name choices are read at runtime from assets/config/instance_names.json, as the GUI does.
- Settings the run adapter warns about (reserved trailblaze power, fuel, update via launcher) are shown
  read-only; after_finish is overridden by the hub (MARCH7TH_AFTER_FINISH=None) and also read-only.
- Not shown at all: notification / update / proxy / cloud-game / telemetry keys (credentials), and
  currencywars_remembrance_trailblazer_name (the player's in-game name).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .. import adapters
from ..adapters.base import read_json
from ..config import GameConfig
from .base import _BOM, Field, Model, Section, SettingsError, SettingsProvider, YamlFile, has_path, set_path


class _Field(Field):
    """ruamel keeps quoted strings as str subclasses ('6' -> SingleQuotedScalarString): still a str choice."""

    def accepts(self, value) -> bool:
        return super().accepts(str(value) if isinstance(value, str) else value)


F = _Field
CONFIG = "config.yaml"

WEEKDAYS = [(1, "周一"), (2, "周二"), (3, "周三"), (4, "周四"), (5, "周五"), (6, "周六"), (7, "周日")]
TEAMS = [(str(i), str(i)) for i in range(1, 13)]          # the file keeps these as strings ('6')
INSTANCE_TYPES = ["拟造花萼（金）", "拟造花萼（赤）", "凝滞虚影", "侵蚀隧洞", "饰品提取"]
ECHO = "历战余响"
CHALLENGE_MAX = {"拟造花萼（金）": 24, "拟造花萼（赤）": 24, "凝滞虚影": 8, "侵蚀隧洞": 6, "饰品提取": 6, ECHO: 3}
TOGGLE3 = [("不配置", "不配置"), ("启用", "启用"), ("停用", "停用")]
RO_HINT = "总控只读显示，要改请在三月七里改（总控预检会提醒这一项）。"

POWER_FIELDS = [
    F("instance_type", "副本类型", "choice", [(t, t) for t in INSTANCE_TYPES],
      help="清体力按这里选的副本类型执行；副本名称也会用于双倍活动。开着“培养目标”时优先刷培养目标的副本。"),
    # instance_names.<类型> / instance_names_challenge_count.<类型> are added in load()
    F("instance_team_enable", "自动切换队伍", help="为特定的副本配置队伍（具体规则请在三月七里改）"),
    F("instance_team_number", "自动切换的队伍编号", "choice", TEAMS, enabled_by="instance_team_enable"),
    F("tp_before_instance", "清体力前传送至任意锚点"),
    F("use_reserved_trailblaze_power", "使用后备开拓力", risk="消耗后备开拓力（单次上限 300 点）", readonly=True,
      help="单次上限300点，全部使用需要将“任务完成后”选项修改为“循环”，然后点击“完整运行”。\n" + RO_HINT),
    F("use_fuel", "使用燃料", risk="消耗燃料（单次上限 5 个）", readonly=True,
      help="单次上限5个，全部使用需要将“任务完成后”选项修改为“循环”，然后点击“完整运行”。\n" + RO_HINT),
    F("merge_immersifier", "体力优先合成沉浸器", risk="把开拓力换成沉浸器",
      help="建议直接通过饰品提取获取位面饰品。达到指定上限后停止。"),
    F("merge_immersifier_limit", "沉浸器上限", "choice", TEAMS, enabled_by="merge_immersifier"),
]

PLAN_FIELDS = [
    F("power_plan", "体力计划", "textarea",
      help="体力计划会在清体力前优先执行，未勾选保留时，完成后会自动从列表中删除。\n"
           "每行一条：副本类型 / 副本名称 / 次数，例如“侵蚀隧洞 / 魔占之径 / 99”。副本名称要和三月七里的一致；"
           "有任何一行写错时整份计划都不会写入（保存后可点“从脚本同步”核对）。"),
    F("power_plan_keep", "保留体力计划", help="开：执行后保留整份计划；关：按剩余次数更新，删除已完成的行"),
]

RELIC_FIELDS = [
    F("break_down_level_four_relicset", "每周清理遗器", risk="每周分解四星及以下遗器（不可恢复）",
      help="每周所选日期清理四星及以下遗器。需要同时启用清体力。"),
    F("weekly_relic_cleanup_day_of_week", "从周几开始清理", "choice", WEEKDAYS,
      enabled_by="break_down_level_four_relicset", help="在游戏刷新时间后执行"),
    F("weekly_relic_smart_discard_enable", "启用智能弃置", risk="智能弃置五星遗器并一并分解（不可恢复）",
      enabled_by="break_down_level_four_relicset",
      help="按不匹配推荐角色和副属性 0 次筛选，分解已弃置及四星以下遗器"),
]

BORROW_FIELDS = [
    F("borrow_character_enable", "强制使用支援角色", help="无论何时都要使用支援角色，即使日常实训中的要求已经完成"),
    F("borrow_scroll_times", "滚动查找次数", "int", min=1, max=10),
]

BUILD_FIELDS = [
    F("build_target_scheme", "识别方案", "choice", [("instance", "副本名称识别"), ("drop", "掉落物识别")],
      help="副本名称识别会进入挑战页读取副本信息；掉落物识别根据列表中的掉落物匹配副本，异常时可尝试切换方案"),
    F("build_target_ornament_weekly_count", "每周饰品提取次数", "int", min=0, max=7,
      help="目标有足够资源后，执行饰品提取的次数，其余时间执行侵蚀隧洞"),
    F("build_target_use_user_instance_when_only_erosion_and_ornament", "仅识别到侵蚀隧洞/饰品提取时使用自定义副本",
      help="开启后，当培养目标仅包含侵蚀隧洞和饰品提取时，清体力将改用你在体力设置中配置的副本"),
]

ECHO_FIELDS = [
    F("echo_of_war_start_day_of_week", "从周几开始执行", "choice", WEEKDAYS,
      help="设为周四表示周一到周三不执行，周四到周日执行"),
    # instance_names.历战余响 / instance_names_challenge_count.历战余响 are added in load()
]

DAILY_FIELDS = [
    F("daily_material_enable", "通过 “合成材料” 完成任务", risk="消耗熄灭原核合成微光原核",
      help="请确保背包中有足够的 “熄灭原核” 用于合成 “微光原核”"),
    F("daily_memory_one_enable", "通过 “回忆一” 完成任务",
      help="请解锁混沌回忆并配置了队伍后再打开该选项（队伍请在三月七里改）"),
    F("daily_himeko_try_enable", "通过 “姬子试用” 完成任务", help="三月七界面里没有这一项，但运行时仍会读取"),
]

ACTIVITY_FIELDS = [
    F("activity_dailycheckin_enable", "每日签到", help="自动领取「星轨专票」或「星琼」，包含巡星之礼、巡光之礼和庆典祝礼活动"),
    F("activity_gardenofplenty_enable", "花藏繁生", help="存在双倍次数时体力优先「拟造花萼」"),
    F("activity_gardenofplenty_instance_type", "花藏繁生副本类型", "choice",
      [("拟造花萼（金）", "拟造花萼（金）"), ("拟造花萼（赤）", "拟造花萼（赤）")],
      enabled_by="activity_gardenofplenty_enable"),
    F("activity_realmofthestrange_enable", "异器盈界", help="存在双倍次数时体力优先「侵蚀隧洞」"),
    F("activity_planarfissure_enable", "位面分裂", help="存在双倍次数时体力优先「饰品提取」"),
    F("activity_journey_highlights_notification_enable", "活动热点通知", help="每次运行时发送带有活动热点截图的通知"),
]

REWARD_FIELDS = [
    F("reward_dispatch_enable", "委托"),
    F("reward_mail_enable", "邮件"),
    F("reward_assist_enable", "支援"),
    F("reward_quest_enable", "每日实训"),
    F("reward_srpass_enable", "无名勋礼"),
    F("reward_redemption_code_enable", "兑换码", help="在日常开头联网拉取并兑换兑换码"),
    F("reward_achievement_enable", "成就"),
    F("reward_message_enable", "短信"),
]

ASSET_FIELDS = [
    F("asset_self_molding_resin_enable", "启用「每月自动合成自塑尘脂」", risk="每月合成 2 个自塑尘脂，消耗合成材料",
      help="每月自动尝试合成 2 个自塑尘脂，成功后记录时间，并在每月 1 号刷新"),
    F("asset_ember_special_pass_enable", "启用「每月自动购买星轨专票」", risk="花余烬在余烬兑换中买满（点最大值）",
      help="每月自动尝试在余烬兑换中购买「星轨专票」，点击最大值并确认后记录时间，并在每月 1 号刷新"),
    F("asset_ember_regular_pass_enable", "启用「每月自动购买星轨通票」", risk="花余烬在余烬兑换中买满（点最大值）"),
    F("asset_ember_tracks_of_destiny_enable", "启用「每月自动购买命运的足迹」", risk="花余烬在余烬兑换中买满（点最大值）"),
    F("asset_lc3_star_superimpose_enable", "启用「3星光锥自动叠加」", risk="消耗三星光锥做叠影",
      help="自动将3星光锥进行叠加以节省背包空间"),
]

CURRENCYWARS_FIELDS = [
    F("currencywars_type", "类别", "choice", [("normal", "标准博弈"), ("overclock", "超频博弈")]),
    F("currencywars_bonus_enable", "自动执行位面饰品快速提取", risk="消耗深度沉浸器",
      help="在领取积分奖励后自动执行位面饰品快速提取消耗深度沉浸器"),
    F("currencywars_rank_difficulty", "职级难度", "choice",
      [("highest", "最高职级"), ("current", "当前职级"), ("lowest", "最低职级")]),
    F("currencywars_strategy", "货币战争策略", "choice",
      [("default", "默认"), ("aglaea", "阿格莱雅"), ("seele", "希儿【测试版】")],
      help="提升晋升等级，推荐在最低职级选择默认策略。提升职级等级，推荐在最高职级选择阿格莱雅或希儿策略。"),
    F("currencywars_strategy_restart_on_special_tags", "遇到特定词条时接受重开",
      help="根据所选策略，在遇到特定词条或词条组合时允许重开"),
    F("currencywars_fast_mode", "启用速通模式", help="开启后，仅在首领节点尝试装备武器，只推荐在最低职级时开启"),
]

DIVERGENT_FIELDS = [
    F("weekly_divergent_type", "类别", "choice", [("normal", "常规演算"), ("cycle", "周期演算")]),
    F("weekly_divergent_level", "难度等级", "choice",
      [(1, "难度 Ⅰ"), (2, "难度 Ⅱ"), (3, "难度 Ⅲ"), (4, "难度 Ⅳ"), (5, "难度 Ⅴ"), (6, "难度 Ⅹ（星阶模式）")]),
    F("weekly_divergent_bonus_enable", "自动执行饰品提取", risk="消耗沉浸器",
      help="在领取积分奖励后自动执行饰品提取消耗沉浸器。\n"
           "注意：v2026.10.16 运行时读的是“模拟宇宙/差分宇宙”里的“自动领取沉浸奖励”，这个开关很可能不起作用。"),
    F("weekly_divergent_stable_mode", "启用低性能兼容模式",
      help="建议仅在低性能设备开启，可以提高事件和随意门交互的成功率（云游戏强制使用此模式）"),
    F("divergent_auto_save_enable", "自动记录差分宇宙存档", help="探索成功后保存到第一个空存档位；存档已满时跳过"),
    F("divergent_station_priority_enable", "站点优先级",
      help="自定义差分宇宙「选择下一站」的站点优先级（优先级表和禁用站点请在三月七里改）"),
]

UNIVERSE_FIELDS = [
    F("universe_category", "类别", "choice", [("divergent", "差分宇宙"), ("universe", "模拟宇宙")]),
    F("divergent_type", "选择差分宇宙时类别", "choice", [("normal", "常规演算"), ("cycle", "周期演算")]),
    F("universe_bonus_enable", "自动领取模拟宇宙沉浸奖励", risk="类别为差分宇宙时，领取积分奖励后做饰品提取，消耗沉浸器",
      help="类别为“模拟宇宙”时，自动领取沉浸奖励"),
    F("universe_frequency", "运行频率", "choice", [("weekly", "每周"), ("daily", "每天")]),
    F("universe_count", "运行次数", "int", min=0, max=34, help="注意中途停止不会计数，0 代表不指定，使用模拟宇宙原版逻辑"),
    F("universe_timeout", "模拟宇宙/差分宇宙超时", "int", min=1, max=24, suffix=" 小时", help="超过设定时间强制停止"),
]

FIGHT_FIELDS = [
    F("fight_timeout", "锄大地超时", "int", min=1, max=24, suffix=" 小时", help="超过设定时间强制停止"),
    F("fight_team_enable", "自动切换队伍"),
    F("fight_team_number", "自动切换到的队伍编号", "choice", TEAMS, enabled_by="fight_team_enable"),
    F("fight_map_version", "地图版本", "choice",
      [("不配置", "不配置"), ("default", "默认（疾跑）"), ("HuangQuan", "黄泉专用"), ("reward", "获得特殊物品")]),
    F("fight_reward_enable", "启用「特殊物品领取」",
      help="按下面三项的选择写入锄大地的配置；关闭时完全不干预锄大地自己那边的设置"),
    F("fight_allow_snack_buy", "购买秘技零食并合成零食", "choice", TOGGLE3, enabled_by="fight_reward_enable",
      risk="选“启用”时每日购买秘技零食材料（花游戏内货币）", help="每日获得特殊物品：购买秘技零食材料并合成零食"),
    F("fight_allow_map_buy", "购买代币与过期邮包", "choice", TOGGLE3, enabled_by="fight_reward_enable",
      risk="选“启用”时每周购买仙舟过期邮包与匹诺康尼艾迪恩代币（花游戏内货币）",
      help="每周获得特殊物品：购买仙舟过期邮包与匹诺康尼艾迪恩代币"),
    F("fight_allow_memory_token", "获得翁法罗斯记忆代币", "choice", TOGGLE3, enabled_by="fight_reward_enable",
      help="每周获得特殊物品：收集翁法罗斯忆质残晶"),
]

LEVEL_HELP = "两个整数：最小层；最大层（队伍配置请在三月七里改）"
FORGOTTENHALL_FIELDS = [F("forgottenhall_level", "关卡范围", "list", help=LEVEL_HELP)]
PUREFICTION_FIELDS = [F("purefiction_level", "关卡范围", "list", help=LEVEL_HELP)]
APOCALYPTIC_FIELDS = [F("apocalyptic_level", "关卡范围", "list", help=LEVEL_HELP)]
LEVEL_MAX = {"forgottenhall_level": 12, "purefiction_level": 4, "apocalyptic_level": 4}

PROGRAM_FIELDS = [
    F("after_finish", "任务完成后", "choice",
      [("None", "无"), ("Exit", "退出"), ("Shutdown", "关机"), ("Sleep", "睡眠"), ("Hibernate", "休眠"),
       ("Restart", "重启"), ("Logoff", "注销"), ("TurnOffDisplay", "关闭显示器"), ("RunScript", "运行脚本"),
       ("Loop", "循环")], readonly=True,
      help="总控运行时用 MARCH7TH_AFTER_FINISH=None 覆盖它，这一项只影响在三月七里手动运行。"
           "电脑的睡眠、关机请用总控运行页的“完成后”。"),
    F("refresh_hour", "游戏刷新时间", "int", min=0, max=23, suffix=" 点", help="用于循环运行及判断任务状态，默认凌晨四点"),
    F("start_game_timeout", "启动游戏超时时间（分）", "int", min=10, max=60),
    F("update_via_launcher", "通过启动器更新游戏", risk="会启动 HoYoPlay 并点击“更新游戏”（下载流量和时间）",
      readonly=True, help=RO_HINT),
    F("auto_set_resolution_enable", "启用自动修改分辨率并关闭自动 HDR",
      help="通过软件启动游戏会自动修改 1920x1080 分辨率并关闭自动 HDR，不影响手动启动游戏（支持国服和国际服）"),
    F("auto_battle_detect_enable", "启用自动战斗和二倍速检测",
      help="游戏启动前通过修改注册表或本地存储开启自动战斗和二倍速，并在清体力、货币战争和逐光捡金场景中检测并保持自动战斗状态"),
]

# (id, title, group, enable_key, table, note)
SECTIONS = [
    ("power", "清体力", "体力", "power_enable", POWER_FIELDS,
     "执行顺序：先跑体力计划；然后开了培养目标就刷培养目标对应的副本，否则按“副本类型”加“副本名称”刷。"
     "完整运行每次都会清体力。\n遗器背包满时三月七会自动分解四星及以下遗器，这一点没有开关。"),
    ("plan", "体力计划", "体力", None, PLAN_FIELDS, ""),
    ("relic_cleanup", "每周清理遗器", "体力", None, RELIC_FIELDS, "需要同时启用清体力；每周到期后在日常开头执行。"),
    ("borrow", "使用支援角色", "体力", "borrow_enable", BORROW_FIELDS, "支援列表（角色和好友）请在三月七里改。"),
    ("build_target", "培养目标", "体力", "build_target_enable", BUILD_FIELDS,
     "根据培养目标刷取行迹与遗器副本，如果无法获取培养目标则回退到默认的副本设置。"),
    ("echo_of_war", "历战余响", "体力", "echo_of_war_enable", ECHO_FIELDS,
     "每周体力优先完成三次「历战余响」，仅限完整运行生效；需要同时启用清体力。"),
    ("daily", "每日实训", "日常", "daily_enable", DAILY_FIELDS,
     "每天按刷新时间做一次；没达到 500 分时下次完整运行还会再试。"),
    ("activity", "活动检测", "日常", "activity_enable", ACTIVITY_FIELDS, ""),
    ("reward", "奖励领取", "日常", "reward_enable", REWARD_FIELDS, ""),
    ("asset", "资产管理", "日常", "asset_manager_enable", ASSET_FIELDS, "每月 1 号刷新的自动合成和购买。"),
    ("currencywars", "「货币战争」积分奖励", "货币战争", "currencywars_enable", CURRENCYWARS_FIELDS,
     "每周刷新后执行。「开拓者•记忆」名称属于个人信息，总控不显示，请在三月七里填。"),
    ("weekly_divergent", "「差分宇宙」积分奖励", "差分宇宙", "weekly_divergent_enable", DIVERGENT_FIELDS,
     "由三月七自己执行，每周刷新后跑。"),
    ("universe", "模拟宇宙/差分宇宙", "差分宇宙", "universe_enable", UNIVERSE_FIELDS,
     "由 Auto_Simulated_Universe 执行。命途、难度、运行模式等请在三月七里改。"),
    ("fight", "锄大地", "锄大地", "fight_enable", FIGHT_FIELDS,
     "由 Fhoe-Rail 执行。优先星球等其余设置请在三月七里改。"),
    ("forgottenhall", "混沌回忆", "逐光捡金", "forgottenhall_enable", FORGOTTENHALL_FIELDS, ""),
    ("purefiction", "虚构叙事", "逐光捡金", "purefiction_enable", PUREFICTION_FIELDS, ""),
    ("apocalyptic", "末日幻影", "逐光捡金", "apocalyptic_enable", APOCALYPTIC_FIELDS, ""),
    ("program", "程序", "程序", None, PROGRAM_FIELDS,
     "总控运行三月七时跳过它的所有暂停，并覆盖“任务完成后”，所以这两类设置只影响在三月七里手动运行。"),
]

# Top-level keys of config.yaml at v2026.10.16 (= assets/config/config.example.yaml, 347 keys).
KNOWN_KEYS = {"config": frozenset({
    'activity_dailycheckin_enable', 'activity_enable', 'activity_gardenofplenty_enable',
    'activity_gardenofplenty_instance_type', 'activity_journey_highlights_notification_enable',
    'activity_planarfissure_enable', 'activity_realmofthestrange_enable', 'after_finish',
    'already_used_codes', 'apocalyptic_enable', 'apocalyptic_level', 'apocalyptic_team1', 'apocalyptic_team2',
    'apocalyptic_timestamp', 'asset_ember_regular_pass_enable', 'asset_ember_regular_pass_timestamp',
    'asset_ember_special_pass_enable', 'asset_ember_special_pass_timestamp',
    'asset_ember_tracks_of_destiny_enable', 'asset_ember_tracks_of_destiny_timestamp',
    'asset_lc3_star_superimpose_enable', 'asset_manager_enable', 'asset_self_molding_resin_enable',
    'asset_self_molding_resin_timestamp', 'auto_battle_detect_enable', 'auto_set_game_path_enable',
    'auto_set_resolution_enable', 'auto_update', 'autoplot_battle_detect_enable', 'autoplot_click_enable',
    'autoplot_enable', 'autoplot_hero_chronicle_enable', 'autoplot_phone_detect_enable',
    'autoplot_remember_enable', 'autoplot_skip_enable', 'autostart_gui_task', 'autostart_headless_task',
    'autostart_minimize', 'autostart_open_gui', 'banner_path', 'borrow_character_enable', 'borrow_enable',
    'borrow_friends', 'borrow_scroll_times', 'break_down_level_four_relicset', 'browser_debug_port',
    'browser_download_use_mirror', 'browser_dump_cookies_enable', 'browser_headless_enable',
    'browser_headless_restart_on_not_logged_in', 'browser_launch_argument', 'browser_mirror_urls',
    'browser_persistent_enable', 'browser_scale_factor', 'browser_type', 'build_target_enable',
    'build_target_ornament_weekly_count', 'build_target_scheme',
    'build_target_use_user_instance_when_only_erosion_and_ornament', 'calyx_golden_preference',
    'check_update', 'close_window_action', 'cloud_game_enable', 'cloud_game_fullscreen_enable',
    'cloud_game_login_timeout', 'cloud_game_max_queue_time', 'cloud_game_use_paid_time',
    'currencywars_bonus_enable', 'currencywars_enable', 'currencywars_fast_mode',
    'currencywars_rank_difficulty', 'currencywars_remembrance_trailblazer_name', 'currencywars_strategy',
    'currencywars_strategy_restart_on_special_tags', 'currencywars_timestamp', 'currencywars_type',
    'daily_enable', 'daily_himeko_try_enable', 'daily_material_enable', 'daily_memory_one_enable',
    'daily_memory_one_team', 'daily_tasks', 'debug_mode_enable', 'divergent_auto_save_enable',
    'divergent_station_disabled', 'divergent_station_priority', 'divergent_station_priority_enable',
    'divergent_team_type', 'divergent_type', 'divergent_universe_daily_completed_count',
    'divergent_universe_daily_completed_timestamp', 'divergent_universe_weekly_completed_count',
    'divergent_universe_weekly_completed_timestamp', 'echo_of_war_enable', 'echo_of_war_start_day_of_week',
    'echo_of_war_timestamp', 'exit_after_failure', 'fight_allow_map_buy', 'fight_allow_memory_token',
    'fight_allow_snack_buy', 'fight_enable', 'fight_main_map', 'fight_map_version', 'fight_operation_mode',
    'fight_path', 'fight_requirements', 'fight_reward_enable', 'fight_team_enable', 'fight_team_number',
    'fight_timeout', 'fight_timestamp', 'forgottenhall_enable', 'forgottenhall_level', 'forgottenhall_team1',
    'forgottenhall_team2', 'forgottenhall_timestamp', 'game_path', 'game_process_name', 'game_title_name',
    'genshin_starRail_fps_unlocker_allow', 'genshin_starRail_fps_unlocker_path', 'home_cards',
    'hotkey_auto_battle', 'hotkey_map', 'hotkey_pause_task', 'hotkey_stop_task', 'hotkey_technique',
    'hotkey_toggle_autoplot', 'hotkey_toggle_autoplot_enable', 'hotkey_warp', 'instance_names',
    'instance_names_challenge_count', 'instance_team_enable', 'instance_team_number', 'instance_teams',
    'instance_type', 'last_run_timestamp', 'launcher_path', 'launcher_process_name', 'launcher_title_name',
    'locales', 'log_level', 'log_overlay_enable', 'log_retention_days', 'loop_mode', 'merge_immersifier',
    'merge_immersifier_limit', 'mirrorchyan_cdk', 'notification_enable', 'notify_bark_autocopy',
    'notify_bark_base_url', 'notify_bark_cipherkey', 'notify_bark_ciphermethod', 'notify_bark_copy',
    'notify_bark_enable', 'notify_bark_group', 'notify_bark_icon', 'notify_bark_isarchive', 'notify_bark_key',
    'notify_bark_sound', 'notify_bark_url', 'notify_custom_data', 'notify_custom_datatype',
    'notify_custom_enable', 'notify_custom_image', 'notify_custom_method', 'notify_custom_url',
    'notify_dingtalk_enable', 'notify_dingtalk_secret', 'notify_dingtalk_token', 'notify_discord_avatar_url',
    'notify_discord_color', 'notify_discord_enable', 'notify_discord_username', 'notify_discord_webhook',
    'notify_gocqhttp_enable', 'notify_gocqhttp_endpoint', 'notify_gocqhttp_group_id',
    'notify_gocqhttp_message_type', 'notify_gocqhttp_token', 'notify_gocqhttp_user_id',
    'notify_gotify_enable', 'notify_gotify_priority', 'notify_gotify_token', 'notify_gotify_url',
    'notify_kook_chat_type', 'notify_kook_enable', 'notify_kook_target_id', 'notify_kook_token',
    'notify_lark_appid', 'notify_lark_content', 'notify_lark_enable', 'notify_lark_imageenable',
    'notify_lark_keyword', 'notify_lark_secret', 'notify_lark_sign', 'notify_lark_webhook', 'notify_level',
    'notify_matrix_access_token', 'notify_matrix_device_id', 'notify_matrix_enable',
    'notify_matrix_homeserver', 'notify_matrix_proxy', 'notify_matrix_room_id',
    'notify_matrix_separately_text_media', 'notify_matrix_user_id', 'notify_meow_enable',
    'notify_meow_nickname', 'notify_merge', 'notify_onebot_enable', 'notify_onebot_endpoint',
    'notify_onebot_group_id', 'notify_onebot_token', 'notify_onebot_user_id', 'notify_pushdeer_enable',
    'notify_pushdeer_token', 'notify_pushdeer_url', 'notify_pushplus_callbackUrl', 'notify_pushplus_channel',
    'notify_pushplus_enable', 'notify_pushplus_token', 'notify_pushplus_webhook', 'notify_qmsg_enable',
    'notify_qmsg_key', 'notify_qmsg_mode', 'notify_qmsg_qq', 'notify_qqbot_appid',
    'notify_qqbot_client_secret', 'notify_qqbot_enable', 'notify_qqbot_group_openid', 'notify_qqbot_openid',
    'notify_send_images', 'notify_serverchan3_enable', 'notify_serverchan3_sendkey',
    'notify_serverchanturbo_channel', 'notify_serverchanturbo_enable', 'notify_serverchanturbo_openid',
    'notify_serverchanturbo_sctkey', 'notify_smtp_From', 'notify_smtp_To', 'notify_smtp_enable',
    'notify_smtp_host', 'notify_smtp_password', 'notify_smtp_plain_text', 'notify_smtp_port',
    'notify_smtp_ssl', 'notify_smtp_ssl_unverified', 'notify_smtp_starttls', 'notify_smtp_user',
    'notify_telegram_api_url', 'notify_telegram_enable', 'notify_telegram_proxies',
    'notify_telegram_thread_id', 'notify_telegram_token', 'notify_telegram_userid', 'notify_template',
    'notify_webhook_body', 'notify_webhook_enable', 'notify_webhook_headers', 'notify_webhook_method',
    'notify_webhook_url', 'notify_wechatworkapp_agentid', 'notify_wechatworkapp_base_url',
    'notify_wechatworkapp_corpid', 'notify_wechatworkapp_corpsecret', 'notify_wechatworkapp_enable',
    'notify_wechatworkapp_touser', 'notify_wechatworkbot_enable', 'notify_wechatworkbot_key',
    'notify_wechatworkbot_webhook_url', 'notify_winotify_enable', 'ocr_gpu_acceleration',
    'ocr_slow_threshold', 'pause_after_success', 'play_audio', 'power_enable', 'power_limit', 'power_plan',
    'power_plan_keep', 'purefiction_enable', 'purefiction_level', 'purefiction_team1', 'purefiction_team2',
    'purefiction_timestamp', 'pypi_mirror_urls', 'python_exe_path', 'redemption_code', 'refresh_hour',
    'reward_achievement_enable', 'reward_assist_enable', 'reward_dispatch_enable', 'reward_enable',
    'reward_mail_enable', 'reward_message_enable', 'reward_quest_enable', 'reward_redemption_code_enable',
    'reward_srpass_enable', 'scheduled_chain_continue_on_failure', 'scheduled_on_conflict',
    'scheduled_run_enable', 'scheduled_run_time', 'scheduled_tasks', 'scheduled_time', 'script_path',
    'start_game_timeout', 'telemetry_enable', 'telemetry_id', 'telemetry_secret', 'tp_before_instance',
    'ui_language', 'universe_bonus_enable', 'universe_category', 'universe_count', 'universe_difficulty',
    'universe_enable', 'universe_enable_gpu', 'universe_fate', 'universe_frequency',
    'universe_operation_mode', 'universe_path', 'universe_requirements', 'universe_timeout',
    'universe_timestamp', 'update_download_proxy', 'update_full_enable', 'update_game_timeout',
    'update_prerelease_enable', 'update_source', 'update_via_launcher', 'use_background_screenshot',
    'use_fuel', 'use_reserved_trailblaze_power', 'weekly_divergent_bonus_enable', 'weekly_divergent_enable',
    'weekly_divergent_level', 'weekly_divergent_stable_mode', 'weekly_divergent_timestamp',
    'weekly_divergent_type', 'weekly_relic_cleanup_day_of_week', 'weekly_relic_cleanup_timestamp',
    'weekly_relic_smart_discard_enable', 'window_height', 'window_maximized', 'window_memory', 'window_width',
    'window_x', 'window_y',
})}
KNOWN_INSTANCE_TYPES = frozenset(CHALLENGE_MAX)   # sub-keys of instance_names / instance_names_challenge_count

# New keys whose value may be a credential or personal data: named in the drift, never shown with a value.
_SENSITIVE = re.compile(r"notify|telemetry|cdk|token|secret|passw|proxy|account|uid|cookie|webhook|url|"
                        r"_name$|_key$|_id$", re.I)


class _M7aYaml(YamlFile):
    """config.yaml in M7A's own layout: ruamel YAML() defaults (width 80, indent 2/2/0).

    The output is re-parsed before a save; it must keep the top-level keys, because M7A moves a file it
    cannot read to config.yaml.bak and continues with defaults."""

    def __init__(self, path: str):
        from ruamel.yaml import YAML
        self.path = path
        self.raw = Path(path).read_bytes()
        self.bom = self.raw.startswith(_BOM)
        text = self.raw.decode("utf-8-sig")
        self.newline = "\r\n" if "\r\n" in text else "\n"
        y = YAML(typ="rt")
        y.preserve_quotes = True
        self._yaml = y
        self.data = y.load(text.replace("\r\n", "\n"))
        if not isinstance(self.data, dict):
            raise SettingsError(f"{os.path.basename(path)} 不是三月七的配置格式")
        self.keys = [str(k) for k in self.data]
        self.exact = self._dump_with(y, self.data) == self.raw
        self.reason = "" if self.exact else "YAML 排版与三月七写的不一致"

    def save(self, backup_file: Path) -> bool:
        new = self.dump(self.data)
        if self.exact and new != self.raw:
            from ruamel.yaml import YAML
            try:
                back = YAML(typ="safe", pure=True).load(new.decode("utf-8-sig"))
            except Exception as e:      # noqa: BLE001 - any parse error means: do not write
                raise SettingsError(f"写回前自检失败（重新解析出错：{e}），没有保存") from e
            if not isinstance(back, dict) or [str(k) for k in back] != self.keys:
                raise SettingsError("写回前自检失败（重新解析后顶层键变了），没有保存")
        return super().save(backup_file)


def _plan_text(plan) -> str:
    return "\n".join(" / ".join(str(x) for x in row) for row in plan if isinstance(row, list))


def _parse_plan(text: str, names: dict) -> list | None:
    """'类型 / 名称 / 次数' per line -> [[类型, 名称, 次数], ...]; None when any line is invalid."""
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in re.split(r"[/／]", line)]
        if len(parts) != 3 or not parts[2].isdigit():
            return None
        typ, name, count = parts[0], parts[1], int(parts[2])
        if not (1 <= count <= 9999) or name == "无" or name not in (names.get(typ) or {}):
            return None
        rows.append([typ, name, count])
    return rows


def _on_set(obj: dict, fd: Field, value) -> None:
    """Root sections: the *_level pairs are typed back as two ints (min <= max); the rest as is."""
    if fd.key in LEVEL_MAX:
        try:
            pair = [int(str(x).strip()) for x in value]
        except ValueError:
            return
        if len(pair) != 2 or not (1 <= pair[0] <= pair[1] <= LEVEL_MAX[fd.key]):
            return
        cur = obj.get(fd.key)
        if isinstance(cur, list) and len(cur) == 2:
            cur[0], cur[1] = pair          # in place: keeps ruamel's layout of the sequence
        else:
            obj[fd.key] = pair
        return
    set_path(obj, fd.key, value)


def _example_comments(text: str, keys: list[str]) -> dict[str, str]:
    """Trailing Chinese comment of each key in config.example.yaml (the tool's own description)."""
    out = {}
    for k in keys:
        m = re.search(rf"^{re.escape(k)}:[^#\n]*#\s*(.+?)\s*$", text, re.M)
        if m:
            out[k] = re.sub(r"\s*true\s*开启[，,]\s*false\s*关闭[。.]?", "", m.group(1)).strip()
    return out


class March7thSettings(SettingsProvider):
    key = "march7th"
    verified_version = "v2026.10.16"
    known_keys = KNOWN_KEYS

    @staticmethod
    def path(g: GameConfig) -> str:
        return os.path.join(g.tool_dir, CONFIG)

    def file_paths(self, g):
        return [self.path(g)]

    def config_names(self, g):
        return [CONFIG] if os.path.isfile(self.path(g)) else []

    def default_config(self, g):
        return CONFIG

    def busy(self, g):
        return adapters.get(g.adapter).running_instances(g)

    def load(self, g, config_name):
        if not os.path.isfile(self.path(g)):
            raise SettingsError(f"找不到 {self.path(g)}")
        f = _M7aYaml(self.path(g))
        root = f.data
        model = Model(files=[f], sections=[])
        names = read_json(os.path.join(g.tool_dir, "assets", "config", "instance_names.json")) or {}
        if not names:
            model.drift.append("读不到 assets/config/instance_names.json，副本名称只能查看")
        self._check_example(g, f, model)

        def name_field(typ: str) -> Field:
            opts = names.get(typ) if isinstance(names.get(typ), dict) else {}
            choices = [(n, "无（跳过）" if n == "无" else f"{n}（{d}）" if d else n) for n, d in opts.items()]
            return F(("instance_names", typ), f"副本名称 · {typ}", "choice", choices, readonly=not choices,
                     help="选项与三月七的副本名称对话框一致（随三月七更新自动跟上）")

        def count_field(typ: str) -> Field:
            return F(("instance_names_challenge_count", typ), f"连续挑战次数 · {typ}", "int", min=1,
                     max=CHALLENGE_MAX.get(typ, 99), readonly=typ not in CHALLENGE_MAX,
                     help="副本最大连续挑战次数（通常不建议修改保持默认即可）")

        types = [str(t) for t in (root.get("instance_names") or {}) if t != ECHO]
        counts = [str(t) for t in (root.get("instance_names_challenge_count") or {}) if t != ECHO]
        extra = {
            "power": [name_field(t) for t in types] + [count_field(t) for t in counts],
            "echo_of_war": [name_field(ECHO), count_field(ECHO)],
        }
        for sub in ("instance_names", "instance_names_challenge_count"):
            present = {str(t) for t in (root.get(sub) or {})}
            if present - KNOWN_INSTANCE_TYPES:
                model.drift.append(f"{sub} 出现了新的副本类型 {'、'.join(sorted(present - KNOWN_INSTANCE_TYPES))}")
            if KNOWN_INSTANCE_TYPES - present:
                model.drift.append(f"{sub} 少了副本类型 {'、'.join(sorted(KNOWN_INSTANCE_TYPES - present))}")

        everything = frozenset(str(k) for k in root)
        for sid, title, group, enable, table, note in SECTIONS:
            if sid == "plan":
                model.sections.append(self._plan_section(root, names, group))
                continue
            fields = table[:1] + extra.get(sid, []) + table[1:] if sid == "power" else table + extra.get(sid, [])
            fields = [x for x in fields if has_path(root, x.key)]       # never add keys M7A does not have
            sec = Section(sid, title, root, fields, enable_key=enable if enable in root else None,
                          note=note, group=group, on_set=_on_set)
            model.sections.append(sec)
            self.audit(model, sec, "config", table, ignore=everything)  # missing keys only (shared root)
            if enable and enable not in root:
                model.drift.append(f"{title}：总开关 {enable} 已不存在")
        self._new_keys(g, root, model)
        if not f.exact:
            model.notes.append("这个文件现在只能查看：" + f.reason)
        model.notes.append("改动在下次启动三月七时生效。推送、更新、代理、云游戏等设置不在这里显示，请在三月七里改。")
        return model

    def _plan_section(self, root, names: dict, group: str) -> Section:
        """power_plan ([[类型, 名称, 次数], ...]) edited as text lines; written back only when every line is valid."""
        view = {"power_plan_keep": root.get("power_plan_keep")}
        fields = [x for x in PLAN_FIELDS if has_path(root, x.key)]
        plan = root.get("power_plan")
        if isinstance(plan, list) and all(isinstance(r, list) and len(r) == 3 for r in plan):
            view["power_plan"] = _plan_text(plan)
        else:
            fields = [x for x in fields if x.key != "power_plan"]

        def on_set(obj: dict, fd: Field, value) -> None:
            obj[fd.key] = value
            if fd.key != "power_plan":
                set_path(root, fd.key, value)
                return
            rows = _parse_plan(value, names)
            if rows is not None and rows != list(root["power_plan"]):
                root["power_plan"] = rows

        return Section("plan", "体力计划", view, fields, group=group, on_set=on_set,
                       note="在清体力（默认副本）之前优先消耗开拓力。")

    def _check_example(self, g: GameConfig, f: _M7aYaml, model: Model) -> None:
        """config.yaml must already be merged with the installed example (else M7A would drop/redo edits)."""
        p = os.path.join(g.tool_dir, "assets", "config", "config.example.yaml")
        try:
            from ruamel.yaml import YAML
            ex = YAML(typ="safe", pure=True).load(Path(p).read_text(encoding="utf-8-sig"))
        except Exception:             # noqa: BLE001 - missing or unreadable example: cannot verify, only report
            model.drift.append("读不了 assets/config/config.example.yaml，无法确认配置已按当前版本补齐")
            return
        if not isinstance(ex, dict):
            return
        same = [str(k) for k in ex] == f.keys
        for sub in ("instance_names", "instance_names_challenge_count"):
            a, b = ex.get(sub), f.data.get(sub)
            same = same and isinstance(a, dict) and isinstance(b, dict) and set(map(str, a)) == set(map(str, b))
        if not same and f.exact:
            f.exact = False
            f.reason = "它和三月七当前版本的模板不一致（更新后还没启动过三月七，或文件被手改过）；" \
                       "请先打开一次三月七（任何任务或界面都会补齐配置），再点“从脚本同步”"

    def _new_keys(self, g: GameConfig, root, model: Model) -> None:
        """Keys added after v2026.10.16: listed read-only in one section, labelled from the example comments."""
        sec = Section("new", "新选项", root, [], group="其他")
        safe = frozenset(k for k in map(str, root) if _SENSITIVE.search(k))
        self.audit(model, sec, "config", [], ignore=safe)
        hidden = sorted(safe - KNOWN_KEYS["config"])
        if hidden:
            model.drift.append(f"另有新选项 {'、'.join(hidden)}（可能含账号或密钥，总控不显示取值）")
        if not sec.new_keys:
            return
        try:
            text = Path(g.tool_dir, "assets", "config", "config.example.yaml").read_text(encoding="utf-8-sig")
        except OSError:
            text = ""
        desc = _example_comments(text, sec.new_keys)
        sec.note = "三月七更新后新增的选项，总控还没有核对过，只读；请在三月七里改。" + "".join(
            f"\n{k}：{desc[k]}" for k in sec.new_keys if k in desc)
        model.sections.append(sec)
