# C17 保存链与 XDR 接入边界

## 已确认的安全路径

当前保存前钩子仅修改 `CameraPicture` 里的完整 HEIC 字节数组副本，再交给原相机保存。补丁只提高已确认的 `tmap`／关联 XMP 上限，不改 EXIF、尺寸、像素或增益图内容。原数组保留，未知或冲突布局拒绝。

这证明的是一条可测试的内存修改路径，**不是所有 C17 新照片已经具备 ratioMax 8×**。

## HardwareBuffer 原写出链

C17 `gn.r` 中，首次写出路径是：

```text
a / c0
  → i0(Uri, byte[], HardwareBuffer, size, captureIdentity)
    或 h0(File, byte[], HardwareBuffer, size, captureIdentity)
  → g0(Uri, File, byte[], HardwareBuffer, size)
  → FormatConverter.writeHardwareBufferToFile(buffer, fd, size)
```

- native 的 int 是 `ParcelFileDescriptor.detachFd()` 返回的 FD。
- native 的 long 是 APS callback 的 `mDataSize`，由保存对象 `r0` 传递。
- 外层另一 long 来自 `CameraPicture.S`，没有作为倍率参数传给 native。
- 未关闭的 HardwareBuffer 路线优先；更换字节数组不能保证改变这条路线。
- native 返回 boolean 后，Java 还可能写 EXIF 和扩展数据。因此它不是整张最终文件完成证明，FD 关闭契约也不能由 Java 方法名推断。

不清空 HardwareBuffer，不绕格式选择，不把 raw buffer 当完整 HEIC。

## APS gainmap 扩展数据

`APSClientCallback.fillFileExtender` 从 native callback Map 取得：

| Map 键 | 保存字段 | 类型 |
|---|---|---|
| gainMapBuffer | mGainMap | byte[] |
| ultrahdrGainMapBuffer | mUHDRGainMap | byte[] |
| ultrahdrGainMapInfo | mUHDRGainMapInfo | byte[] |

这些字段继续经过 `PictureResultHelper`、`CameraApsExtendData`，进入 `CameraPicture.c`。

`gn.r.Q` 条件执行扩展包装，将数据交给：

```text
local.uhdr.gainmap.data
local.uhdr.gainmap.info
OplusFileExtenderHelper.setExtensionData(String, byte[])
```

原 Java 顺序是首次图像写出、条件 EXIF、扩展包装；部分 APS 自带扩展分支跳过 Java 包装。

**还不知道 gainmap-info 字节的版本、布局、端序、倍率单位，也未证明 blob→HEIF tmap 的实际序列化函数。**它不能因为名字带 gainmap 就被当成已确认的 62 字节 tmap。Palette 界面的 Android Gainmap 辅助实现也不能代替原生 capture 证据。

## 新拍范围：不能使用墙钟猜测

保存对象 `CameraPicture.S` 的 HEIC 路径来源是：

```text
resultinfo_identity
  → Long.parseLong
  → ApsResult.mIdentity
  → CameraImage.mTimestamp
  → CameraPicture.S
```

恢复待保存任务也可以沿用相同 identity，因此：

- 不将 S 和 `System.currentTimeMillis()` 比较来猜“启动后新拍”。
- 不以保存对象新建、输出 callback 到达、保存完成 callback 非空来证明新 capture。
- 不用路径时间、图库扫描或文件安静时间补范围证据。

严格新拍门需要核实“新请求／实际 capture 回调→输出 identity→保存 owner”的关联，并排除持久化旧任务恢复。已核实当前请求会在消费者中退化为 tag／hash，APS merge identity 还可能从全局值回退；尚未找到非重放的输出 owner 关联。新的身份策略仅在 `tests/support/XdrCaptureScopePolicy.java` 做研究回归，没有注册或编入正式 hook，不把研究模型当成该关联已经实现。

## 尚缺的实效材料

同一次本机 C17 拍摄需要对齐：原保存前完整容器、原 gainmap-info blob、最终样张，以及真实消费／序列化路径。没有这些材料，不新增猜偏移的 native metadata 修改，不退回落盘截断、rename 或 quiet-size 写回。

本记录仅来自按需源代码核查，没有在手机上安装、拍照、写文件、替换库或修改安全策略。

## 本地证据入口

相对 `_tmp/decoded-PJX110_17.0.1.201/`：

- `sdk/smali/com/oplus/ocs/camera/consumer/apsAdapter/APSClientCallback.smali`
- `sdk/smali/com/oplus/ocs/camera/consumer/apsAdapter/APSClient.smali`
- `sdk/smali/com/oplus/ocs/camera/consumer/ApsProcessor.smali`
- `sdk/smali/com/oplus/ocs/camera/appinterface/adapter/PictureResultHelper.smali`
- `camera/smali/com/oplus/camera/aps/util/CameraApsExtendData.smali`
- `camera/smali_classes7/gn/r.smali`
- `camera/smali_classes7/com/oplus/camera/jni/FormatConverter.smali`
- `camera/smali_classes7/com/oplus/camera/module/processor/storageprocessor/i.smali`
