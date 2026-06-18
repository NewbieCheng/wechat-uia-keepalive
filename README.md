# wechat-uia-keepalive

微信 4.x UIA 前置工具 — 模拟 Windows 无障碍客户端，暴露完整 UI 树，为自动化（如 pyweixin）做准备。

**ChaseZ 制作** | 版本 v1.1.0

## 背景

微信 4.x 默认只向普通自动化暴露 `Qt51514QWindowIcon` 外壳窗口。当有合法的 **UI Automation 无障碍客户端**持续接入并遍历控件树时，微信才会切换为 `mmui::MainWindow` / `mmui::LoginWindow`，完整 UI 结构才可见。

本工具用 **comtypes + IUIAutomation** 模拟类似「讲述人」的行为，无需启动 Narrator。

## 功能

| 功能 | 说明 |
|------|------|
| 前置引导 | 先预热无障碍客户端，再启动微信，等待 UI 树暴露 |
| UI 探测 | 检测当前 UI 是否可见（`mmui::*`） |
| 控件树导出 | 按深度 dump 控件，便于调查 |
| Keepalive | 持续遍历 UIA 树，保持暴露状态 |
| 环境面板 | 显示微信版本、安装路径、进程与 UI 状态 |
| 日志 / 诊断 | 自动写日志，可导出诊断报告 |

## 快速开始（推荐）

1. 从 [Releases](https://github.com/NewbieCheng/wechat-uia-keepalive/releases) 下载 `WeChatUIA-Tool.exe`
2. 双击运行，或双击同目录下的 `启动工具.bat`
3. 选择菜单 **1** 执行完整前置引导（默认 6 分钟预热后启动微信）
4. 引导完成后可输入 **2** 反复探测、**3** 导出控件树、**8** 导出诊断报告

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
  0. 退出
```

## 命令行

```powershell
WeChatUIA-Tool.exe                    # 交互菜单
WeChatUIA-Tool.exe bootstrap          # 完整前置流程
WeChatUIA-Tool.exe probe              # 单次探测
WeChatUIA-Tool.exe dump               # 导出控件树
WeChatUIA-Tool.exe keepalive          # 持续 Keepalive

# 调试：缩短预热时间
WeChatUIA-Tool.exe bootstrap --launch-delay 60
```

## 日志与排查

- 日志目录：exe 同目录下 `logs/`
- 日志文件：`WeChatUIA-YYYYMMDD.log`
- 诊断报告：`logs/diagnostic_YYYYMMDD_HHMMSS.txt`（菜单 8 导出）

常见问题：

| 现象 | 可能原因 |
|------|----------|
| UI 一直 `Qt51514QWindowIcon` | 预热时间不足；需先运行本工具再启微信 |
| `window_not_found` | 微信未启动 |
| 多窗口时探测失败 | 工具会自动优先选择 `mmui::MainWindow` |
| 账号仍不可见 | 部分账号被微信限制 UIA，与工具无关 |

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

## License

MIT

## 作者

**ChaseZ**
