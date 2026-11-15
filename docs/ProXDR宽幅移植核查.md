# ProXDR 与 XPAN 宽幅移植核查

## 2026-11-15 双版本目标更正与最新实测

用户明确：C16的XPAN模式正常，但ratioMax8×未生效；手机升级C17后需重新适配，同时保留C16。8×是两代共同待修目标，不是旧版已实现效果。C16/C17各自精确profile、SDK和配置契约保持，C17原生入口不得复用到C16或删掉旧支持。

C17新片`IMG20261115084848.heic`实际元数据仍4.95656277×；真实FINAL holder为type9/watermarkMode0/source=crop8192×3024/canvas8504×5000/placement156,988、TOP/BOTTOM素材，无overlay，Java增益宽高格式键全部缺失。故当前Java补丁未进入增益合成，且APS直接保存绕过完整数组8×入口。补拍完成后已退出相机，不修改任何原图。

实际原生注册路径进一步确认为`/odm/lib64/libwatermark_photo.so`，它与APK内同名库不同，不使用此前APK帮助库相同结论证明原生渲染一致。正在只读核查真实插件与gain-only边界，结果见 [APS原生入口核对](APS原生入口核对.md)。

## 2026-11-14 实机验收：尚未修好

用户后续明确授权无重启实测，最新配套调试APK已安装并实拍两张。真实8192×3024照片内容、相框与gain半比例位置对齐已核；但相框内装饰增益仍主要174，两张实际tmap/XMP上限都约4.95656277×，不是8×。

- 当前`w`实测为tag/info均XPAN、`overlay=false`，旧v4条件补丁跳过；数值参数尚不完整，不能强扩门槛。
- `c0`实测`bytes=0`、`heifProcessInAps=true`，APS直接编码保存，原完整数组8×入口没有生效。倍率目标仍8、只升不降，未降低目标或事后改图。
- 最新配套包是 `_tmp/xpan-device-package-3l1qempg/XpanAce3Pro-v2.0.zip`（ZIP `1f43fdff90cde7c050cdff3f77e4ac3f45c41e4c20e827434831d61a2559e660`，APK `52ef02f292dbdb1f996c8a7b8bd07394ec67058a6658bf4537b8e6b4daa7c059`），仅本机调试，不发布。
- 本轮没有手机重启、清数据、卸应用、修改其它模块或LSPosedDB；下面仅本地及未安装陈述均为对应旧阶段历史。

详见 [真实样张与失败日志](C17实机相框验收.md)。本地287项回归不替代本次实拍结论。

## 用户要求与范围

重点是补齐原生 ProXDR 对 XPAN 宽幅、尤其相框成片的适配，而不是只将 metadata 上限提高到 8×。保持独立 XpanAce3Pro v2.0/code20、默认8192×3024、仅1×、原 C16 支持及水印隔离。

本轮用户选择仅本地修复，不更新、强停、拍摄或重启手机；提供的原 HEIC 保持不变。

## 已知事实

- 样张已经具有真实 HEIF gain-map 与 tmap，不能据此称 ProXDR 引擎完全不存在，也不能反过来称所有宽幅支持已经正确。
- 主照片 ROI 8192×3024、增益照片 ROI 4096×1512，位置均恰半。相框区域却有增益174与0的大块差别，并有 gain 内的logo/文字裁切。
- 原 file 的 log2 maximum≈2.30934（linear≈4.95656）。只改上限8×会保留这些空间像素；已实际解码证明修改前后主图、gain-map 像素和尺寸不变。
- 第一阶段参数修复只覆盖已核实的 C17 Y8/stage3/无框原 gain source，沿原水印路径隔离主相框素材；RGB、原 gain 已含框等情况仍保持原流程。不能把此子集说成全 ProXDR 宽幅移植已完成。

## 2026-11-14 继续调试修复：保持 ratioMax=8×

用户继续要求调试修复并保留8×。本阶段仍没有新增手机安装、强停、拍摄或重启，未发布。与上一阶段只做 native 原型不同，以下标准 XPAN 条件参数修复已进入配套 Hook APK。

### 实际修复

- 旧 `XpanGainMapFrame` 只按 `zk.c.e(tag.q)` 的 AI-frame 判定接受候选，不能据此把标准哈苏相框当作同一分支。该原谓词也依赖全局支持／水印状态，不是仅比较一个滤镜字符串。
- 新增对**实际 holder**的严格识别：`ys.g.y=3`（XPAN 水印mode）、`ys.c.N=9`（JSON creator）、`ys.g.z0` 的 literal `styleId="hassel_style_xpan_1"`、`ys.g.Z=3`（FINAL），并要求 `k==p`、已核无框照片／gain pair、Y8和全部精确几何校验。`k/l` 是配置尺寸／坐标，不泛化成原 gain 内部真实照片 ROI。
- 在本次 `w` 内创建独立黑底、空装饰素材参数，继续原 native 分配、复制和所有权流；不写主图、raw Map 或 borrowed gain。
- 标准候选的原 `e` 两轴缩放被 AI 谓词挡住时，只在**精确本次 gain e 调用**将原 false 结果改为 true；不同holder／tag／尺寸、嵌套 e/w、线程、门关闭、候选变化、原异常均不改。不修改全局 AI 支持，也不替换原 `e/w` 返回对象。
- 最后 Gate 调用之后再复核 snapshot，阻止资格回调修改 style／stage／Map 后旧快照仍放行；三个真实副作用拒绝场景已加入回归。
- 原 gain 已含内框、RGB、未知裁剪或不匹配时保留原流程，输出本次格式、stage、source／crop／canvas／placement等诊断；不打印指针、路径、照片内容或任意JSON。不能说该子集已覆盖用户样张实际 raw 分支。

### 8× 与真实样张验证

`XdrBeforeSave` 和 `XdrRatioPatch` 的倍率策略代码未改变，仍传入 `8.0`，已有更高上限仍不降低。真实样张分析副本修改16字节后为 `ratioMax=8.0 / log₂=3.0`，再次处理 `ALREADY_SUFFICIENT`、同一数组引用。原 HEIC 始终为原哈希。

使用独立 libheif1.23.4 对该新副本逐像素解码：主图42,520,000像素、gain10,630,000像素全部与原先独立解码结果一致，差异均为0。它证明**倍率补丁不改像素**，不是标准相框修复的手机显示验收。

- 分析副本：`_tmp/xpan-ratio8-sample-vby5owya/analytical-ratio8.heic`，SHA-256 `050079a42e9c5539294a2d6eaf69dd80c18da078c6991769b4c097900813c3da`。
- 独立解码报告：`_tmp/ratio8-decode-output-_achz6ej/report.json`。
- 完整本地回归：**287项通过，432.591秒，退出码0**；最终门控副作用修复后相框／生产范围专用回归另11项通过。最初ABI错误fixture未真改变字段、旧源码禁用规则仍禁止新限定谓词改写曾失败；保留失败结果，修正为真正负例与精确scope断言后重跑，未删失败行为或放宽几何门槛。

### 当前唯一交付候选

`_tmp/xpan-device-package-21_8x2ne/XpanAce3Pro-v2.0.zip`

- 207,547字节；SHA-256 `11df775bb06e8b2c8a34fbe9c5eb2b703ade6455fdf791aa526972cd3146d372`。
- **SIGNED/DEVICE TEST ONLY**：沿用用户已授权的现有调试证书，证书SHA-256 `916348b3987d0f557f6aa6ae480822c18a91aeba2b9e46cbf4af30b295e43f9c`；不是正式发布证书，不生成新key，不开云更新。
- 配套 APK SHA-256 `275d7db31c1a7e6b445162233e191367d7cbdb5605b17f776f02054aae5efbbf`，源码pin／DEX／签名／版本20/2.0／双版本三配置配套／单包白名单已核验；180个DEX类仅为溯源，不是生效数量。
- native AHB画布原型未入包，未替换系统库。没有上传、提交、推送或发布，没有覆盖桌面v1.3。

当前标准子集修复可进入另行授权的手机调试；但实际raw输入与frame输出、最终8×、图库显示仍须验证。不得凭本地模拟保证“所有XPAN相框已修好”。

## 本轮对照层次

1. **原生 XPAN 请求**：对照本机7.013.51与一加13参考7.006.123，核对UltraHDR开关、格式、尺寸、crop／gain生成请求。
2. **APS算法配置**：区分应用feature protobuf、算法调试说明资产与真正aps_capture_configs，确认缺失的模式／尺寸声明。
3. **便携native helper**：只比较对应的水印/gainmap/HEIF帮助库，检查JNI、依赖及实现；相同的库重复移植不会补能力，SoC绑定算法库不能直接搬。
4. **显示端限制**：相机保存布局与图库ProXDR识别/显示不是同一层；没有取得图库实现时不替它下结论。

本机原厂C17材料、一加13相机/SDK/三配置已经实际提取并解码。只移植有明确缺失和兼容证据的部分，不采用整套 donor APK/SDK/HAL 替换，不用删日志或宽松哈希检查代替实效。

## 已确认的对照结果

### 直接搬一加 13 的帮助库无效

对应版本的以下库整文件 SHA-256、Build ID、JNI/依赖和关键实现分别完全相同：

- `libWaterMark-Jni.so`
- `libwatermark_photo.so`
- `libFormatConverter.so`
- `libgallery_fileextender.so`

donor 与本机 RGB888 填充循环都有同样的字节／像素宽度混用：循环每次写3字节却以像素 width 为字节终止值，只填每行约1/3像素。该缺陷在两版相同，且 JNI 对非零 buffer 固定返回0，不验证所有lock/fill结果。它不能被直接搬库修复；也没有证据样张一定走RGB888，不能把这一分支直接定为样张根因。

FormatConverter 只写已有缓冲字节到FD，FileExtender只封装已有扩展blob；不能把它们称为生成宽幅 gain-map 的新算法。

### XPAN 原生 UltraHDR 请求已有

两版 XPAN 均从 `com.oplus.feature.ultra.hdr.enable` 的 String `on/off` 配置生成请求，并将 `mbUltraHdrEnable` 转为 native `ultra_hdr_enable=1/0`。最终capture还有feature／scene拒绝条件。未发现可证明为宽幅必需且donor独有的直接调用。

本机多了 scene49 的 capture UltraHDR 拒绝分支，但该次XPAN是否命中未知，不解除此判断。`xpan_mfll_out_width/height` 是主图输出，不是 gain-map ROI 尺寸；主图已有crop/ratio传递不能证明native增益图效果。

### 新 APS 算法表原件已定向取得

`_tmp/donor-aps-config-rjzpuqkv/oplus_camera_algo_switch_config`

124,808字节，SHA-256 `c0a72bff99eef1f5a43b5d7a469afc0adf5afcb35199b4684ee8700651246ec1`；只按精确inode读取，分区目标读取44,371字节。原件为不透明封装。

已进一步定向提取同一 donor payload 的匹配 `libOplusStringJNI.so` 与 `libOplusSecurity.so`，核实唯一 native 解码路径、完整 UTF-8／注释感知 JSON、PKCS#7 和重加密一致性，得到未改写的明文：

`_tmp/donor-aps-native-plaintext-ilz97nlu/oplus_camera_algo_switch_config.plaintext.jsonc`

124,786字节，SHA-256 `60f8ec4155e26a5d631ded849b4292662b9eb7fc75c420bcb3d376e058a58778`；file_version=15.001、aps_version=6、aps_mode=1。没有套用C16固定偏移、密钥／模式／padding枚举，原件仍保留同一哈希。

其 xpan@0 capture 是 enable=1、filter=1、upscale=0、watermark=1、rotate_mirror=1，没有独立的HDR／gainmap节点可从donor搬来。这不否定原生HDR，只说明此算法开关表没有给出缺失补项。

实际SDK加载优先 `/apex/com.oplus.camera.aps/etc/camera/config/`，不存在才用ODM，RUS还能按版本覆盖。尚没有当前本机C17实际生效的匹配算法表／APEX内容，所以不能把donor旧表直接移植或认为缺引擎。

### 当前 raw gain 的照片 ROI 不能从保存结果反推

本次样张的最终主图与增益图照片区域恰好按1/2对齐，说明问题不在将整张带框画布强改为65:24，而在相框区域的增益内容。原 gain 输入是否已经包含内框，仍没有当前拍摄运行值证明。

C17 的 `watermark_image_display_rect` 与 `watermark.params` 四值有真实写入链，但由 hold 的放置坐标与 `s.p`／XPAN `s.k` 生成，不是独立的原照片子矩形。当前 `ol.q0.f` 在调用 gain `w` 之后才生成这些 Bundle／footer 参数；不能提前复用它们来解释 raw gain，尤其不能对可能已有内框的输入只靠宽高比裁切。

APS 的 `srcImageCropRegion` 确有 `byte[]` 透传并保存为 `crop.region` 的链，但其字节格式、坐标空间及与 raw gain 的关联尚未证实，不把它当作可直接反射的 Rect。模板 `imageOffset.startX/startY` 只是输入 buffer 在新画布中的放置位置，也不能替代输入内部的原照片 ROI。

因此，本地画布实现仅消费调用者明确提供、已经核对坐标空间的 ROI；缺失、冲突或不能精确关联时拒绝处理。没有这些运行契约前不扩展正式 Hook 到“原 gain 已含框”分支。

### 官方源码核对的实际范围

已对现有本地源码做具体核对，不把引用仓库网址当作完成证据：

- Ace 官方 modules/devicetree 本地 Git 分支为 `oneplus/sm8650_b_16.0.0_ace_3_pro`，revision `a7ab73a78d71452a1f3bd4c04e2f8aa9ea4ea014`，目标为 [OnePlusOSS 官方仓库](https://github.com/OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8650)。`vendor/qcom/proprietary/camera-devicetree/oplus/corvette-camera-overlay.dts` 与 HEAD 无差异，SHA-256 `aa0d1bf627333ff589a48fb7fa41276eb8313c7e747d1041ae29b3df40634846`。11–18行确认平台／common／actuator include、pineapple compatible及项目23814；该顶层文件没有 XPAN／HDR／gain-map 尺寸声明。没有展开 include，不能外推整个相机内核，更不将 C16 分支当成当前 C17 完全一致的源码。
- 现有本地一加13目录的 common 源码版本为6.6.118。`fs/erofs/erofs_fs.h`（SHA-256 `3cea004fe2fbe4366dd4c05605fc552821e59a92c05e9ef771722c8efe086400`）及 `fs/erofs/zmap.c`（`1b158d339c2b2e550530fd94d55aeaa880ab4a85705761d05210c30e2a1b94bc`）实际支持 compact mapping 的普通 `base+1`、big-pcluster `base+0` 与 CBLKCNT 计数规则，对应本轮按需读取 donor 的文件系统格式。该6.6目录没有已核到的Git／下载凭据，固定 OnePlusOSS revision仍未证；文件哈希只标识当前本地内容。

这份源码核对用于文件系统读取和设备标识／接口边界，不是可移植的 ProXDR gain-map 生成源码，不能宣称官方宽幅算法已经被搬入模块。

### 显示端尚不能定论

相机Java保存与控制接口未见4:3／16:9硬拒65:24的宽高比白名单。已有支持名单是mode字符串，图库有独立ProXDR开关。当前未取得匹配图库的渲染实现，不会宣称图库层已核完或所有比例均支持。

## 本地原型与实现边界

已实现小型、格式与 stride 明确的增益画布组件：使用 Android 公共 AHardwareBuffer 接口，只复制调用者显式指定的同空间照片 ROI，ROI 外活动字节清零，不读写行 padding。不复用主图白色相框素材；RGB888 按整行像素宽度×3处理，Y8按单通道处理。

源码位于 `tests/support/gain_frame_native/`，构建入口为 `tools/build_gain_frame_prototype.py`。只放测试支持目录及项目 `_tmp` 构建输出，没有 JNI 或正式 APK 接线，不替换系统库。对 source 借用引用、producer 完成断言、格式／stride／边界、分配、双侧 lock/unlock 失败有明确拒绝与清理；producer 完成仍是调用方需提供的运行契约，不由编译成功证明。

### 2026-11-14 最终本地验证

- 原型完整编译回归 **7 项测试通过**；host 正常运行覆盖 **7,560 项断言**。最终使用本机已有 MSVC，未下载安装工具、未接受 SDK 许可；选定的工具集版本与初始化环境严格一致。
- Android arm64/API29 库实际编译，使用真实 NDK 公共头；ELF64/AArch64、唯一导出 `xpan_gainframe_clone_neutral`，依赖仅公共系统库，无 vendor 或 `libc++_shared.so`。该原型库 SHA-256 为 `b6d56c2683ea4a5b3c2160b604ddb8e38864f21e97b6f36cc05a2d722bf75f2d`。
- 像素回放、C17 Java 条件补丁与生产范围守卫完整 **15 项测试通过**；工具集固定／路径引用修正后，像素回放4项再次通过。Java 原流程测试不是设备 native 像素证明。
- 最终工具链的 **ASan／UBSan 均 unavailable，未报通过**。ASan 干净程序无法运行；MSVC 忽略 undefined 选项且故意溢出未被诊断。先前 Zig 路线的 UBSan 通过只属于先前构建，不充作最终构建验收。
- 本轮曾有 C++ 头文件缺失、编译超时、MSVC `fopen` 弃用警告被 `/WX` 拒绝，以及回放期间源码变化导致 provenance 校验拒绝；均保留失败记录，修复后重跑，不删失败项或放宽边界。

最终构建证据：`_tmp/gain-frame-prototype-jkvyf2u0/provenance.json`。

### 真实样张像素回放

通过 `tools/replay_gain_frame.py` 将已独立解码的 `4252×2500` gain PNG 输入同一份 C++ core，显式使用最终 gain 空间 `[78,494,4174,2006]`。这不是从 raw 运行输入猜矩形，也不是照片文件写回。

- **6,193,152** 个照片像素的全部增益字节原样保留，变化为 **0**。
- **4,436,848** 个外框像素的增益字节全部为0；其中 **2,351,456** 个像素由非零改为0。
- Y8 与 RGB888 两次真实回放结果逐像素一致；source、行 padding 与两端 guard 不变。
- 原 HEIC SHA-256 仍为 `49c3365b2ace06fcb316a147ff14d8ddb01fcf05b9e19d2316780fe0db3dd76c`，没有重新编码、写回或改 EXIF／主图。

最终证据：

- Y8：`_tmp/gain-frame-replay-gqekd87v/provenance.json`
- RGB888：`_tmp/gain-frame-replay-09ixeded/provenance.json`

输出 `analytical-bytezero-gain.png` 只是增益数据分析图，不是修复后的 HDR HEIC。增益图里的 logo／文字被清零不代表删除主图水印；主图完全没改。

本样张的 SDR-base／min0／offset0／gamma1 metadata 使零字节对应不额外提亮，但原型没有读取或生成每拍 metadata，不能把零字节泛化为所有编码下的中性值。相框增益清零也不能证明未知 raw gain 的照片区域本来正确，或所有后续编码／图库显示已经闭环。

### 不接正式 APK 的条件

尚缺当前 raw gain 的独立照片 ROI、同拍／同 stage 坐标关联、生产者完成和缓冲生命周期、每拍增益 metadata 契约；缺这些条件时不接正式 Hook 到原 gain 已含框／RGB等分支，不把最终样张坐标硬编码到相机。也没有生成可刷修复包、安装手机或进行图库 HDR 显示测量。

它是画布适配实现，不是从零生成 HDR 增益或 HEIF 编码器，更不能称全 ProXDR 引擎已经移植。不得将“导出符号一致”“metadata parser通过”或“模块注册成功”作为原生 ProXDR 宽幅／相框效果通过声明。
