# daily-hub · 游戏日常总控

**Daily-task hub for mobile games on PC.** 一个窗口里按顺序跑完多款游戏的日常：自动打开游戏 → 调用各游戏现成的自动化脚本 → 判断跑完 → 关掉脚本和游戏 → 下一款。

总控本身不操作游戏，它负责调度你已经在用的这些开源脚本：

| 游戏 | 脚本（需要自行安装、先在脚本里配置好） | 游戏怎么启动 |
| --- | --- | --- |
| 原神 | [BetterGI](https://github.com/babalae/better-genshin-impact)（一条龙） | 总控启动游戏 |
| 绝区零 | [ZenlessZoneZero-OneDragon](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon)（一条龙） | 脚本自己启动 |
| 明日方舟：终末地 | [MaaEnd](https://github.com/MaaEnd/MaaEnd) v2.33+（实例里加前置任务“🚀启动游戏（PC）”） | 脚本自己启动 |
| 鸣潮 | [ok-ww](https://github.com/ok-oldking/ok-wuthering-waves)（日常任务） | 总控启动游戏 |
| 崩坏3 | [Maa_bbb](https://github.com/miaojiuqing/MAA_bbb) | 总控启动游戏 |
| 崩坏：星穹铁道 | [三月七小助手](https://github.com/moesnow/March7thAssistant) | 脚本自己启动 |
| 明日方舟 | [MAA](https://github.com/MaaAssistantArknights/MaaAssistantArknights)（MuMu 模拟器 12） | 总控启动模拟器 |
| 碧蓝航线 | [Alas](https://github.com/LmeSzinc/AzurLaneAutoScript)（MuMu 模拟器 12） | 总控启动模拟器 |

不想跑的游戏取消勾选即可；顺序可以调整，每款也能单独运行。

## 功能

- **一键日常**：按顺序逐款运行，状态一目了然（待运行 / 运行中 / 已完成 / 异常/跳过 / 已停止），实时日志。
- **判定结果**：读取各脚本自己的日志判断“跑完了 / 出错了”，跑完后关闭脚本和游戏（先正常关闭，超时再结束进程）。
- **预检**：只读检查路径、配置名、已在运行的实例，以及脚本里可能误伤的设置（如完成后关机、强制结束进程），并提示会消耗资源的选项（体力药、源石、储蓄电量等）。
- **日常设置页**：像 Alas 那样在总控里直接改各脚本自己的日常设置（任务开关、顺序和选项）。
  - 改之前自动备份，可以还原；
  - 脚本运行中只读，载入后文件被改过时拒绝保存；
  - 脚本在自己界面里改了设置，这里会自动同步。
- **脚本更新检查**：每次打开总控都会检查各脚本有没有更新。版本变了，就核对判定用的日志文字还在不在、设置项有没有增减，并生成一份报告。
- **定时运行**：用 Windows 计划任务把电脑从睡眠中唤醒，跑完日常后再睡眠或关机（可选）。

## 下载与使用

1. 到 [Releases](../../releases) 下载 `DailyHub-v*-win64.zip`，解压到任意文件夹（例如 `D:\Games\DailyHub`）。
2. 双击 `DailyHub.exe`。需要管理员权限，会弹一次 UAC；这些游戏和脚本大多要求管理员权限，总控整体以管理员身份运行。
3. 打开“**总控设置**”，填好每款脚本的**工具目录**（输入框里有灰色的示例路径），游戏程序路径默认是官方启动器的默认安装位置，不一样就改掉。点“保存设置”。
4. 回到“**运行**”页点“**预检**”，按提示处理红色 ✗ 的问题。
5. 先对单款点“**独立运行**”试一次，确认没问题再用“**一键日常**”。

工具目录示例：

| 脚本 | 工具目录示例（文件夹里应该有） |
| --- | --- |
| BetterGI | `C:\Program Files\BetterGI\BetterGI`（BetterGI.exe） |
| ZenlessZoneZero-OneDragon | `D:\Games\ZenlessZoneZero-OneDragon`（src 和 .venv 文件夹） |
| MaaEnd | `D:\Games\MaaEnd`（MaaEnd.exe） |
| ok-ww | `C:\Users\<你的用户名>\AppData\Local\ok-ww\data\apps\ok-ww`（working 文件夹） |
| Maa_bbb | `D:\Games\Maa_bbb`（MFW.exe） |
| 三月七小助手 | `D:\Games\March7thAssistant`（March7th Assistant.exe） |
| MAA | `D:\Games\MAA`（MAA.exe） |
| Alas | `D:\Games\AlasApp\AzurLaneAutoScript`（alas.py） |

### 定时运行（可选）

在 `DailyHub.exe` 所在文件夹打开**管理员 PowerShell**：

```powershell
powershell -ExecutionPolicy Bypass -File .\schedule-task.ps1 -Time 05:00 -Then sleep        # 每天 5:00
powershell -ExecutionPolicy Bypass -File .\schedule-task.ps1 -TestInMinutes 5 -Then sleep   # 5 分钟后演练一次（不跑游戏）
powershell -ExecutionPolicy Bypass -File .\schedule-task.ps1 -Remove                        # 删除
```

- 唤醒后电脑必须停在桌面，不能停在锁屏，否则脚本没法操作游戏。设置方法：设置 → 账户 → 登录选项 → “如果你已离开，Windows 应在何时要求你重新登录”选“从不”。
- 各游戏一般 4:00 刷新，定时请设在 4 点之后。

## 注意

- 总控只负责调度。每款游戏的日常内容（刷什么、要不要用体力药等）都在对应脚本里设置，或者在总控的“日常设置”页设置。
- 自动化脚本可能违反游戏的用户协议，请自行承担风险。本项目与各游戏厂商、各脚本作者均无关。
- 运行时请不要操作鼠标键盘，以免干扰脚本。

## 从源码运行 / 构建

需要 [uv](https://docs.astral.sh/uv/) 和 Windows 10/11：

```powershell
uv sync                                                      # 建环境（Python 3.12 + PySide6）
.\启动总控.cmd                                               # 运行（会弹 UAC）
uv run --with pyinstaller python tools/build_release.py     # 打包成 dist\DailyHub-v*-win64.zip
```

代码结构：`src/daily_hub/adapters/` 每款脚本一个适配器，负责启动命令、日志判定和预检；`src/daily_hub/settings/` 是日常设置页各脚本的读写；`runner.py` 是队列；`gui.py` 是界面。

## 许可证

[MIT](LICENSE)
