# OmniCam Ace 3Pro

OmniCam-Muse（Find X9 Ultra 相机增强模块）思路在**一加 Ace 3 Pro**（PJX110，相机 6.020.876）上的独立移植。
与 X-Pan 模块（XpanAce3Pro）**分开**、并存使用。

## 当前状态

| 功能 | 机制 | 状态 |
|---|---|---|
| 独立设置应用（高像素 8192 开关 / 状态卡 / 日志导出 / 即时生效） | aapt2+javac+d8 应用 + su | ✅ 代码完成，待实机 |
| 相机静音（快门/对焦/倒计时全关，无需系统静音） | hook ql.g 播放入口 | ✅ 代码完成，待实机 |
| 按模式记忆闪光灯 | hook BaseMode.buildStreamSurface + DataManager 隔离 | ✅ 代码完成，待实机 |
| XPAN 快门声 | SoundPool 空资源替换（Muse 手法） | ⏳ 音源资源映射待 smali 深挖（音效文件名已混淆） |
| 画质自然化（AI 细节增强/LCE 关） | conf_tuning_params 权重归零 | ❌ 不适用：6.020.876 的调参文件无这些段落（只有 TMCParamsSet） |
| 高像素直出条件放宽 | 决策配置条件 | ❌ 不适用：6.020.876 高像素为手动模式，决策配置无该条件 |
| 色彩家族/LUT | libBasicTone 槽位（X9U 实现） | ⛔ 暂缓：6.020 管线不同，需实机评估 |
| 理光GR/POP/移轴 | 需他机相机资源+原生补丁 | ⛔ 排除 |

## 使用

1. KernelSU 刷入模块（自动装设置应用与钩子，并完成 LSPosed 启用）
2. 桌面打开「OmniCam 设置」，按 KernelSU 授权提示允许 root
3. 改动即时生效（自动重启相机进程）

## 验证状态声明

**本轮全部为离线完成，未经实机验证。** 手机回来后执行 `adb-selftest.sh` 并逐项核对。
