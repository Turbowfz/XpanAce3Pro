# XpanAce3Pro — 一加 Ace 3 Pro 哈苏 X-Pan 宽幅模式

给一加 Ace 3 Pro（PJX110）的相机开启哈苏 **X-Pan 65:24 宽幅模式** 的 KernelSU 模块，
配合 LSPosed 使用。**卸载即还原**，不改动系统分区。

> 作者：**Turbowfz**（Turbo）
> 模块本体：`XpanAce3Pro-v1.0.zip`（可直接刷入的稳定版本）

---

## 功能

- 开启相机 **X-Pan 65:24 宽幅模式**（哈苏实体皮肤 UI + 显影动画 + 胶片滤镜）
- 拍出 **4096×1512（65:24）** 宽幅照片，带哈苏水印
- 变焦条 1× / 2×
- 修复切换模式后的界面休眠、退出残留、快门锁死等一系列问题
- **卸载模块即完全还原**（hook APK 会自动卸载）

## 环境要求

| 项目 | 要求 |
|---|---|
| 机型 | 一加 Ace 3 Pro（PJX110，SM8650） |
| 系统 | ColorOS 16，相机版本 **6.020.876** |
| Root | KernelSU |
| 框架 | **LSPosed**（作用域：相机） |

其他机型 / 相机版本未测试，不保证能用。

## 安装

1. KernelSU 刷入 `XpanAce3Pro-v1.0.zip`
2. 重启（模块会自动安装 hook APK 并在 LSPosed 中启用相机作用域）
3. 打开相机 → 菜单里进入 **XPAN** 模式

## 卸载

在 KernelSU 中移除模块并重启。模块会自动清理 LSPosed 作用域与 hook APK，
`/odm/etc/camera/config/` 下三个相机配置由模块目录卸载自动还原。

## 原理

1. **配置对齐**：往 `/odm/etc/camera/config/` 放入对齐过的相机配置
   （`camera_unit_config`、`camera_unit_feature_config.protobuf`、`oplus_camera_config`），
   让系统认识 X-Pan 模式与 800T 胶片滤镜族
2. **尺寸修正 hook**（LSPosed）：X-Pan 申请的出流尺寸（1920x864 + 4096x1512）
   本机 HAL 不认，hook 改成实测能跑的组合 **预览 2304x1048 + 拍照 4096x1512**
3. **界面修复 hook**：X-Pan 的界面容器在部分进入路径下不会被应用驱动
   （内部状态停在 -1），hook 在超时后补一次初始化，让哈苏皮肤 / 显影动画 / 滤镜条正常出现

详细踩坑记录见源码内注释（`hook/src/com/xpanport/hook/XpanHook.java`）。

## 已知限制

- 变焦只有 **1× / 2×**（0.6× 超广角在本机的成像通路未调通，已按稳定性移除）
- 出片为 **HEIC** 格式（presenter 激活后的正常行为）
- 模式切换后边框 / 滤镜条可能**慢半拍**出现（等应用自己完成初始化，避免抢跑）

## 仓库结构

```
├── XpanAce3Pro-v1.0.zip   # 可直接刷入的模块包
└── hook/                  # LSPosed hook 源码
    ├── AndroidManifest.xml
    ├── build.sh           # 一键编译（Windows Git Bash）
    ├── assets/xposed_init
    └── src/
        ├── com/xpanport/hook/XpanHook.java   # 核心尺寸/界面修复
        ├── com/xpanport/setup/Enabler.java   # LSPosed 作用域自动启用
        └── de/robv/android/xposed/…          # Xposed API 桩（仅编译用）
```

## 编译 hook

```bash
cd hook
bash build.sh   # 需要 Android SDK (build-tools 35 + platform android-35) 与 JDK 17
```

产物 `out/XpanHook.apk` 可单独重装（模块的 `payload/XpanHook.apk` 就是它）。

## 免责声明

- 仅供学习研究，刷入产生的任何问题由使用者自行承担
- `system/odm/etc/camera/config/` 下的三个配置文件提取自一加 ColorOS 16 固件，
  版权归一加/OPPO 所有，仅用于个人设备恢复默认相机行为，请勿商用

## License

MIT
