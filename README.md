# wechat-uia-keepalive

微信 4.x UIA 前置工具 — 模拟 Windows 无障碍客户端，暴露完整 UI 树，为自动化（如 pyweixin）做准备。

**ChaseZ 制作** | 版本 v1.3.1

## 背景

微信 4.x 默认只向普通自动化暴露 `Qt51514QWindowIcon` 外壳窗口。当有合法的 **UI Automation 无障碍客户端**持续接入并遍历控件树时，微信才会切换为 `mmui::MainWindow` / `mmui::LoginWindow`，完整 UI 结构才可见。

本工具用 **comtypes + IUIAutomation** 模拟类似「讲述人」的行为，无需启动 Narrator。

## 功能

| 功能 | 说明 |
|------|------|
| 三套 CPU 渲染预设 | 社区推荐 / 全软件栈 / ANGLE WARP，失败可切换重试 |
| 就绪检查 | 启动前检查微信是否已退出、权限、DPI 等 |
| 前置引导 | 先预热无障碍客户端，再启动微信，等待 UI 树暴露 |
| UI 探测 | 检测当前 UI 是否可见（`mmui::*`） |
| 控件树导出 | 按深度 dump 控件，便于调查 |
| Keepalive | 持续遍历 UIA 树，保持暴露状态 |
| 环境面板 | 版本、路径、进程、UI 状态、渲染模式、预设、就绪检查 |
| 操作历史 | 可翻页查看历史记录，刷新后不丢失 |
| 日志 / 诊断 | 自动写日志，可导出诊断报告 |

## 提高成功率（推荐流程）

微信 4.1+ 能否暴露完整控件树，取决于**两条链路同时满足**：

1. **渲染模式**：GPU 加速时顶层常为 `Qt51514QWindowIcon`（空壳）；CPU 软件渲染更易出现 `mmui::MainWindow`
2. **无障碍客户端**：须在本工具预热期间持续 UIA 附着与遍历，微信才会加载完整 Provider

**标准操作：**

1. **完全退出微信**（托盘也退出）
2. 运行工具，查看面板「就绪检查」为 **通过**
3. 选 **1** 执行前置引导（默认 6 分钟预热 + 启动微信）
4. 等待 UI 树状态变为 **已暴露 (mmui::MainWindow)**
5. 引导期间保持工具运行，或选 **4** 持续 Keepalive

**若引导失败（仍为 Qt 外壳）：**

1. 完全退出微信
2. 选 **9** 切换到下一套渲染预设（`community → full_software → angle_warp` 循环）
3. 再次执行引导（菜单 **1** 或 **9**）

> 无法 100% 保证成功：微信有无障碍检测私有逻辑，部分账号/环境仍可能被限制。

## 三套渲染预设

| 预设 ID | 面板名称 | 环境变量 | 适用场景 |
|---------|----------|----------|----------|
| `community` | 社区推荐（默认） | `QT_OPENGL=software` | [VidiBot 社区最常引用方案](https://www.vidibot.com/2115.html) |
| `full_software` | 全软件栈 | 上述 + `QT_QUICK_BACKEND=software` | Qt Quick 仍走 GPU 时的加强版 |
| `angle_warp` | ANGLE WARP | 上述 + `QT_ANGLE_PLATFORM=warp` | 需 ANGLE 软件光栅化兜底时 |

环境变量仅在**进程启动时**生效。微信已在运行时，工具不会自动结束进程，只会提示先退出再重试。

## CPU 软件渲染启动

| 变量 | 说明 |
|------|------|
| `QT_OPENGL=software` | 强制软件 OpenGL（不走显卡） |
| `QT_QUICK_BACKEND=software` | Qt Quick 软件后端 |
| `QT_ANGLE_PLATFORM=warp` | WARP 软件光栅化兜底 |

面板字段含义：

- **渲染模式** — 当前微信是否由本工具以 CPU 模式启动
- **渲染预设** — 下次启动将使用的预设
- **就绪检查** — 启动前环境是否满足条件

## 客户界面

交互模式采用**固定状态面板 + 可翻页操作历史**布局：

- 按 **2** 反复探测，结果写入历史区，**不会输出 JSON**
- 按 **↑/↓** 或 **PgUp/PgDn** 翻页查看历史
- 按 **9** 切换渲染预设并重试引导

## 快速开始（推荐）

1. 从 [Releases](https://github.com/NewbieCheng/wechat-uia-keepalive/releases) 下载 `WeChatUIA-Tool.exe`
2. **先完全退出微信**
3. 双击运行，或双击同目录下的 `启动工具.bat`
4. 确认「就绪检查」为通过后，选 **1** 执行完整前置引导
5. 失败后选 **9** 换预设重试

## 菜单说明

```
  1. 前置引导（默认 6 分钟后启动微信）
  2. 快速探测 UI 是否可见
  3. 导出控件树（调查用）
  4. 持续 Keepalive（Ctrl+C 停止）
  5. 前置引导（自定义预热秒数）
  6. 刷新状态 / 环境信息
  7. 打开日志目录
  8. 导出诊断报告
  9. 切换渲染预设并重试引导
  0. 退出
```

## 命令行

```powershell
WeChatUIA-Tool.exe                              # 交互菜单
WeChatUIA-Tool.exe bootstrap                    # 完整前置流程
WeChatUIA-Tool.exe bootstrap --render-preset full_software
WeChatUIA-Tool.exe probe                        # 单次探测
WeChatUIA-Tool.exe dump                         # 导出控件树
WeChatUIA-Tool.exe keepalive                    # 持续 Keepalive

# 调试：缩短预热时间
WeChatUIA-Tool.exe bootstrap --launch-delay 60

# 自动化脚本（输出 JSON Lines）
WeChatUIA-Tool.exe probe --jsonl
WeChatUIA-Tool.exe bootstrap --jsonl
```

## 日志与排查

- 日志目录：exe 同目录下 `logs/`
- 日志文件：`WeChatUIA-YYYYMMDD.log`
- 诊断报告：`logs/diagnostic_YYYYMMDD_HHMMSS.txt`（菜单 8 导出）

常见问题：

| 现象 | 可能原因 | 建议 |
|------|----------|------|
| 渲染模式「需重启才生效」 | 微信已在运行 | 完全退出后，用工具重新引导 |
| UI 一直 `Qt51514QWindowIcon` | GPU 渲染或预热不足 | 换预设（菜单 9）+ 确保 6 分钟预热 |
| 就绪检查「DPI 待处理」 | 系统缩放 125%+ | 微信属性 → 兼容性 → 高 DPI → 系统 |
| 就绪检查「权限不一致」 | 工具与微信管理员状态不同 | 两者都用或都不用管理员 |
| `window_not_found` | 微信未启动 | 先完成引导或手动启动 |
| 多窗口探测失败 | 多个微信窗口 | 工具优先选 `mmui::MainWindow` |
| 账号仍不可见 | 微信账号限制 UIA | 与工具无关，换账号或环境测试 |

系统因素影响（参考 [VidiBot 研究](https://www.vidibot.com/2115.html)）：显卡驱动、DPI 缩放、Win10/Win11 差异、多显示器、管理员权限均可能影响 Qt 渲染 backend。

## 从源码运行

```powershell
pip install -r requirements.txt
python wechat_uia_tool.py
```

## 自行打包 exe

```powershell
pip install -r requirements-build.txt
build_exe.bat
# 输出: dist\WeChatUIA-Tool.exe
```

## 与 pyweixin 配合

前置引导成功且 UI 已暴露后，可在同一台机器上使用 [pyweixin](https://github.com/NewbieCheng/pywechat) 等自动化库。引导期间请保持本工具或 Keepalive 运行。

## 系统要求

- Windows 10 / 11（64 位）
- 微信 4.x（Weixin.exe）
- 无需安装 Python（使用 Release 中的 exe 时）

## 原理参考

- [微信 4.1+ UI 自动化说明](https://blog.csdn.net/weixin_26763955/article/details/159909455)
- [Weixin 4.1+ Qt 框架与渲染模式研究](https://www.vidibot.com/2115.html)
- [pywechat Weixin 4.0 说明](https://github.com/Hello-Mr-Crab/pywechat/blob/main/Weixin4.0.md)

## License

MIT

## 作者

**ChaseZ**
