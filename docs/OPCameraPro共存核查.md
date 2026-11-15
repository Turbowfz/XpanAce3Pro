# OPCameraPro 共存核查（2026-11-15）

## 目标与边界

- 独立 XpanAce3Pro 尽量与 OPCameraPro 共存，不把长期停用或卸载它当作默认前提。
- 只修改 Xpan 自身。保留 C16 `6.020.876`、C17 `7.013.51` 两个精确分支，以及默认8192×3024、仅1×、水印隔离和ratioMax至少8×的目标。
- OPCameraPro 的 Java／LSPosed 钩子与附属 Root 模块的 SDK 覆盖分开核验；发现一项 SDK 冲突不等于所有 Java 功能不能共存。
- 本阶段没有安装、强停／重开相机、拍照、启停或改写OPCameraPro、重启手机；没有改其他项目、LSPosed数据库、SELinux、系统库或原HEIC。

## 已落实：水印无写入不补偿

原事务首次进入XPAN时，未知的自有水印设置继承当前值；目标与当前值相同时也不写入。但原 `restore()` 只在 `!wrote && !staged` 时免回写。一次已经暂存、尚未实际提交的只读切换失败，仍会把四个旧快照值写回，可能盖掉其他钩子在原切换中更新的设置。

现在仅在本模块确实尝试过写入或删除时补偿。只读事务失败、取消、门控撤销、抛异常或代次过期时不读写 live store，也不改变模式状态、自有存档或另一模式的备份。没有新增延迟修复、默认值或对另一模块的设置改写。

`wrote=true` 仍在调用 setter／remover **之前**设置，保留“先改值、后抛异常”的恢复保护。真正写过的失败前缀仍尝试全部四键并回读；清理失败继续保留备份、阻止后续交换。本轮不扩大已写事务的回滚语义。

此修复使用两代共用的生产 `WatermarkState`，不是在测试中重新实现一套状态机。

## XPAN 参数的直接冲突与本地隔离

OPCameraPro 3.1.22 确有配置返回值覆盖：在SDK `UpdateHelper.getValidConfigData` 原返回后解析配置，写入自有注册的六个XPAN键，再返回新字符串；不是只改UI标签。主图尺寸为`7872x2912`，portrait／landscape裁剪为`360,64,2912,7872`／`64,360,7872,2912`，版本3、legacy0、support1，受它的XPAN开关／版本分支控制。

此入口未见当前模式限制，但所改键本身专属于XPAN。它与Xpan当前已核定的原生`4096×1512`和`780,0,1512,4096`／`0,780,4096,1512`并不一致。现有C17流尺寸补丁只处理surface元组／wrapper；app裁剪会单独进入拍摄`com.oplus.opx.CropInfo`，SDK MFLL输出尺寸会单独进入APS。不能说8192流尺寸补丁必然兜住全部请求参数，也不能仅据静态路径宣称OP已导致当前实拍失败。

新增 `XpanC17Config` 只在精确C17分支对上述三个XPAN键做局部getter修正：app `getSizeConfigValue`、两个数组getter以及SDK主尺寸typed／raw getter。前摄、非XPAN键、未知SDK Key／错误类型和其他配置都走原流程。三个原生值保持既有配套配置基线；最终8192×3024仍由原出流补丁负责，没有把native源尺寸改成8192或事后放大照片。

全部10个ABI（8个方法、2个只读实例字段）验证后才注册6个回调，注册期间空转，成功后仍需C17核心与ModuleGate就绪；失败撤回，残钩保持无效。数组每调用复制。SDK typed调用先遮蔽raw修正，再读Key元数据；门控检查后用已核字段只读复核，不再次调用可能有副作用的元数据getter。原解析／异常／嵌套回调结束后无条件恢复遮蔽；不把同名错误类型的Key硬改成Size字符串。

另对原SDK `XpanMode.getSurfaceSize` 的整个同步调用设置负向排除：该方法所有stream内部读取主尺寸都保持原流程，避免RAW／DOL的`Pair.first`被局部getter间接改变。已知YUV／JPEG／HEIC的出流修正仍由原C17核心按stream、camera和format执行；未知格式不通过新层改尺寸。

不在OP加载入口争priority，不覆盖整份JSON、不写共享配置map、不改OP模块或滤镜组。两种API的默认priority不证明真实执行先后；完整共存仍需实机验证。

静态证据均在 `_tmp/opcamera-coexist-dex-k_544nso/decoded/smali/`：

- `ᛱᛱᲈᲀᲀ.smali:64,140,180` 与 `ᛱᛱᛱᤝᲀ.smali:1995`：配置hook与新字符串返回。
- `ᛱᛱᛵᛶᛴ.smali:51794–52941`：六个XPAN键注册和值。
- `ᛱᛱᛸᛶᲇ.smali:118–201`：另外读取XPAN静态滤镜组并`addBack`，不是feature-list副本；本轮不改它，不据此推断污染普通模式。
- `ᛱᛲᛴᲈᤝ.smali:19142–19314`：从OP自有`files/product/framework`复制两SDK至`files/SubModule/system/product/framework`。这是生成路径，不代替已有设备hash／实际挂载证明。

没有定位到OP针对ZoomSeekBar、getFeatureInfoList或四个目标水印pref键的直接回调，结论是**未证，不是不存在**。Xpan C17点位列表和feature返回值保留其他结果后复制，仅在XPAN／对应生成作用域修改。C16原实现保留；未证的slider退出时序候选不猜字段或加新权限。

已有补充静态核查还发现OP会追加XPAN算法配置／protobuf模式，以及一个共享`CameraPictureImage.getCurrentZoomSize()`返回`3072×4096`的分支（未见逐次模式判断）。这些不能当作“OP无钩子”，也不是新层已覆盖的证明。本轮不接管其全局配置或按默认priority硬抢结果；实际启用条件、C17执行次序与照片结果仍需分项验收。

## SDK 硬冲突仍不能放行

此前真实设备证据已确认：OPCameraPro `3.1.22` 的附属 Root 模块覆盖了两份 C16 SDK，而手机相机是C17 `7.013.51`。C17调用新增取消拍摄和内存查询接口；把旧 SDK 加入白名单不能补齐语义，也不能把带取消参数的调用改成无参数拍摄。

Xpan仍核验当前实际unit共享库路径、两份SDK内容、三份配置与安装实例。配置父层兼容不替代原件或SDK身份检查，不为OP存在本身新增一刀切禁止。

此前临时停用OP及恢复本机原C17 SDK只属于对应已授权诊断，不能当作后续自动停用许可。完整共存需要在保留C17原厂SDK的前提下分别验收Java钩子与Root覆盖；本阶段未改OP模块负载或设置，也不声称已证明其提供了可分离开关。

证据和恢复边界见 [C17冲突恢复与父层兼容](C17冲突恢复与父层兼容.md)。

## 本地验证

- 新回归先在修前失败：`AssertionError: read-only rollback clobbered other module settings`。失败日志保留于 `_tmp/opcamera-watermark-red-lgvvm5el/result.txt`。
- 修后水印生产helper **1747项检查通过**；包含首次继承、已知同值／缺省值、进出XPAN、其他钩子实时改值、未提交／未知实际模式、门控撤销／抛异常、嵌套过期与重复回调。真实写入／删除后失败和清理重试的原测试仍通过。
- 水印阶段的两代ABI、普通模式边界、profile、ModuleGate、C16配置、C17模式／菜单／UI、安装身份、签名与打包共 **149项专项测试通过**。日志：`_tmp/opcamera-coexist-regression-jg3ml31n/result.txt`；该结果早于新增C17几何层。
- 加入C17几何层及入口守卫后的对应专项 **150项通过**：`_tmp/opcamera-coexist-c17-final-regression-rhf9cv4c/result.txt`。最后的两字段末端复核由下列新层测试和完整编译另行覆盖，未沿用旧APK。
- 新几何层 **17项测试、93／93个JVM场景、1898项断言通过**，包含53个错误ABI变体、普通／前摄保留、缓存不变、RAW／DOL排除、门控参数变化、嵌套／异常归还、丢失句柄与残钩拒绝重装。日志：`_tmp/c17-config-resumed-run-6pbo3rsn/result.txt`。
- ZCode进程中断前的一轮仅有部分日志，含ABI场景失败且未完成，保留于`_tmp/c17-config-run-0VrtVrpM/output.txt`，**没有算作通过**；恢复后重新验证的是最后13份Java源码，未复用旧测试编译结果。
- 原XDR解析、两代保存前入口、拍摄范围研究和gain-frame参数共 **10项保留回归通过**：`_tmp/opcamera-coexist-xdr-preservation-reyu4ea7/result.txt`。未修改原8×策略；不据本地通过称新片8×生效。
- 最后源码的完整编译与DEX结构验证通过；水印、入口守卫和打包集成 **64项再次通过**：`_tmp/opcamera-coexist-integration-ikwhdbpr/result.txt`。**本地代码与包装验证不是双模块实机共存证明。**

## 2026-11-15 C17 补全换装实验：兼容结论

用户要求把 OP 附加模块的补全文件从 C16 全部换成 C17 测试兼容。**没有使用一加13 donor 包**（异机 SDK 变体会破坏本机相机），全部使用本机自己的原厂 C17 原件（`d2b20133…`/`7418d599…`，来源 `/my_product/product_overlay/framework`）。

### 执行过程

- OP 模块目录 `/data/adb/modules/OPCameraPro/system/product/framework/` 两个 JAR 由 C16 换为本机 C17（777→644 修正权限）。OP 私有源目录（`files/product/framework`、`files/SubModule`）实测不存在，无第二处需要换。
- C16 原件双备份：设备端 `/data/local/tmp/xpan-op-enable-test/op-c16-backup/`（哈希核验）＋本地 `_tmp/xpan-op-enable-test/op-c16-backup-copy-uxsbdt5b/`。
- 重启后 OP 的 C17 覆盖生效（有效 SDK = C17，由 OP overlay 挂载）；`/odm/etc` 回到 KSU overlay（kind=ksu，inode 136:32）——证实该父层 overlay 来自 OP 模块挂载，OP 停用时为 kind=normal。
- 本 Xpan 按设计流程重新批准＋安装（boot 8170f77a、token `6a732450…`），与 OP 同时激活。

### 兼容结论（真机验证）

| 项目 | 结果 |
|---|---|
| 两模块同进程同时加载 | 通过 — OP Modern API＋本 Xpan 全套钩子共同安装，无资格拒绝 |
| 相机稳定性 | 通过 — 冷启动、XPAN 进入、拍摄全程无崩溃（PID 4427） |
| XPAN 模式完整工作 | 通过 — 哈苏皮肤/XPAN徽标/胶片取景框正常，实拍出片 |
| XPAN 输出正确性 | 通过 — 主图 8192×3024、gain 4096×1512、tmap/XMP 完整（`74b02228…`） |
| 本 Xpan 钩子行为 | 通过 — gain-frame 遇 OP 环境未知参数时安全跳过（fail-closed），无乱改 |
| OP 钩子健康 | 通过(有警告) — 相机内仅 `camera_unit_config is null`（OP 自身警告，非致命）；另一进程的读配置失败为 OP 既有行为 |
| 8× | 未变 — 仍约 4.95656×，是已记录的原生缺口，不是共存引入的回归 |

**结论：OP 附加模块换用本机 C17 补全后，与本 Xpan 完全兼容**——C16 SDK 冲突就此消除。这是首个 OP 全功能开启＋XPAN 完整可用的真机状态。

### 边界与注意

- OP 是按 C16 SDK 构建的，其部分功能在 C17 下可能有未观察到的行为变化（本帧只验证了相机稳定与 XPAN 链路；用户日常使用中若发现 OP 特定功能异常，属 OP 自身对新 SDK 的适配问题，与本模块无关）。
- 8×、相框增益、水印设置完整通知等原有未完成项不变。
- 恢复方法：把备份的两个 C16 JAR 复回 OP 模块目录（或保留 C17 版本），按需重启。删除本实验设备端备份前须确认已不需要还原。
- 证据：`_tmp/xpan-op-enable-test/`（换装/重启/共存日志、照片、几何分析全链）。

## 正式版自动替换机制（v2.0）

从 v2.0 正式版起无需手工换文件。安装与每次开机 `service` 启动时执行 `xpan_op_compat`：

1. **先判系统**：`ro.build.version.release` 必须为 `17`（C17）；C16 或其他系统直接跳过，一个字节都不写。
2. **再判模块**：`/data/adb/modules/OPCameraPro/module.prop` 的 `id=OPCameraPro` 且 framework 目录存在；不存在或身份不符只记日志。
3. **随包 C17 优先**：`payload/op-compat/framework/` 携带本机原厂 C17 两个 JAR，`MANIFEST` 记录逐文件 SHA-256。目标文件已是该哈希 → `current`；不是 → 先备份到 `/data/adb/xpan-runtime/op-compat-backup/` 并校验，再原子替换、回读校验，日志写 `replaced; reboot makes it effective`。
4. **C16 滤镜/影调素材隐去**：仅当 OP 仍携带已知 C16 补全（两个 JAR 任一等于固定 C16 SHA-256）时启用。把 `system/odm/etc/camera` 下的 `meishe_lut`、`filters_lut`、`basictone` 三个目录和 `gamma_*_conf.json` 文件改名为 `*.xpan-c16-hidden`（文件全部保留，可逆），让本机 C17 原厂素材透出；`config/`、`is_mount`、SDK JAR 及其他文件不动。OP 以后自带 C17 补全时自动不再改名。
5. **其余补全文件按原厂同步（尽力而为）**：OP 模块 `system/product/framework`、`system/odm/etc/camera` 下每个文件尝试与设备同路径比较；相同不动，不同则备份后同步。注意：OP 生效时该路径本身被 OP 模块覆盖，比较会读回 OP 自己的文件，因此对已被 OP 覆盖的文件这项实际不产生写入；真正生效的是第 3、4 步和原厂 lower 层透出。
6. **安全边界**：符号链接、源缺失/源字节不符、写入失败 → 保持原文件并记状态；不碰 disable/remove 标记、不 umount、不卸载、不动其他模块。KernelSU 开机早期挂载模块文件，替换后需重启生效；已生效时重复执行只记 `current`。
7. **恢复 C16**：把备份目录对应文件复制回 OP 模块、把 `*.xpan-c16-hidden` 改回原名后重启。

本地专项 11 项覆盖：C17 精确替换与备份幂等、C16 系统完全不动作、非 C17 字节被核对源替换、附属模块不存在、身份不符、源缺失/源字节不符、目标缺失、disable 标记不动、其余补全文件按原厂镜像同步（含备份路径）、C16 滤镜/影调素材只在 C17 且 OP 仍为 C16 补全时隐去（含 config 不动与幂等）、OP 已是 C17 补全时素材不动作、生产守卫（C17 门槛、固定 SHA-256、无危险命令）。打包专项 48 项含“随包补全字节必须等于固定原厂哈希”。

## 最新真机共存观察与候选包

按后续单独许可，最新配套包已经无重启安装，Xpan与OPCameraPro Modern API在相机PID11575、22664中同时加载；XPAN进入／拍摄、无框8192×3024和普通模式拍摄已观察。OP附属Root模块disable标记未改、有效SDK仍为原厂C17，故只证明这轮Java部分同进程运行及有限场景，不证明旧SDK覆盖可共存或OP全部功能完成验收。详见[当前包实机验收与调试](C17当前包实机验收与调试.md)。

- 当前包：`_tmp/xpan-aps-trace-package-ypk9jfm1/XpanAce3Pro-v2.0.zip`，221341字节。
- ZIP SHA-256：`9a846c4be70ea9f7b53567ba4c9546eea9e10f08866f7adabc601bd7b8e2fd78`。
- 配套APK SHA-256：`1a96d7e4601d31ae5a8e96fcd7ca0b83d0ae856048f855c9fbc9e37d96407a0e`。
- 当前实例：`a734505c-7051-42eb-ab47-117a4c2a0cf5`；active1/profilec17、APStrace已关闭、原照片未改。8×与相框增益仍未通过，不发布成完整功能版本。

## 上一轮本地配套单包（继续测试阶段）

本轮完整337项本地回归通过，新联合钩子16场景通过；后续脚本与模块声明完善的完整记录见[继续测试与模块完善](20261115继续测试与完善.md)。仍未安装或验收双模块实机共存。

- 文件：`_tmp/xpan-continued-testing-package-77i8x693/XpanAce3Pro-v2.0.zip`。
- 版本仍为`2.0 / code20`，内部包名`com.xpanport.hook`，显示名`XPanForAce3Pro`。
- ZIP大小：218779字节。
- ZIP SHA-256：`ff4cd8c2205fcaffff7273975fedb666a9f05f6cbc55118369a8048fce1afa6c`。
- 配套APK SHA-256：`beb4b635ce553728432c2e330c5d99070ce96379e258e346dbdfdaaccfc16c41`。
- 使用项目已有调试证书，仅本机验收，不是发布证书；未安装、提交、推送或发布，不替换v1.3发布ZIP或云更新。
- 完整构建、独立签名／对齐验证、配套打包通过；最后13份Java源码hash与包内provenance完全一致，包含新C17几何层。两个profile及配套APK均已复核，根目录携带完整v1.0–v2.0简洁日志，无新增native payload。

前面的水印单项中间包`_tmp/xpan-opcamera-coexist-package-lov0tsqh/`已被新增源码取代，仅作过程记录，不是当前交付。

本轮没有设备变动；最近已核验的手机APK仍为此前诊断包`dabd7e59…`，本地新APK`beb4b635…`尚未安装，不能把新日志或修复写成设备已生效。C16的8×未生效、C17三张实拍约4.95656×及相框增益未通过的结论不变。
