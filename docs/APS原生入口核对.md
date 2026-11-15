# 本机 APS / HEIF 原生入口核对（并行，2026-11-14）

## 范围

只读当前 Ace 3 Pro C17 相机实际加载的库。未安装新包、重启/强停手机、拍摄、修改系统库或照片。本机模式/固件保持7.013.51 / PJX110_17.0.1.201(CN01)。原8×策略不降低；本次不能凭ABI核对宣称native修复完成。

相框与8×实际未通过证据见 [实机相框验收](C17实机相框验收.md)。本轮日志已单独精简为完整v1.0–v2.0记录并加入模块根目录；技术细节只记本文件，不写进发布更新日志。

## 本机来源已核验

`_tmp/aps-readonly-baseline-2igghpfm/metadata.txt` 是相机PID21893的真实maps与元数据。此次正确root引用下读取成功，没有调整SELinux、ptrace或通过注入绕过权限；之前Permission denied记录仍保留，不将失败当作已读取。

| 实际映射路径 | 大小 | SHA-256 |
|---|---:|---|
| `/product/lib64/libAPSClient-cmd-jni.so` | 1,115,608 | `559cd5e9fc533cbce3cc3390cecad74127167daaf3e964e0cec424fcbc3eff9c` |
| `/odm/lib64/libAlgoProcess.so` | 7,525,632 | `674c0aff67973677f205fd64bf74144997cc77bf09cbd823a4c666570d8a299b` |
| `/odm/lib64/libapsultrahdr.so` | 473,536 | `5343be9c2fd4dc706ae6fdf05190cc8083f714006a2eac9d99ec77fee60ec02f` |
| `/system_ext/lib64/libHeifEncoderWrapper.so` | 22,896 | `ac5336b87acf75f70dda303c12559dc94de9fb115498240217a43a3b3d4fef72` |
| `/system_ext/lib64/liboplusheifwriter.so` | 178,200 | `ebff86619e87236d3cbbefb22f962e55aaad1dfcfd8d69d92ac32e40ec58d5ca` |
| `/system/lib64/libstagefright.so` | 2,410,720 | `7f332f5b9a29b0983869c92f177d9108cd3d60326d22d7463eb779aa46398fa4` |

均按映射路径定向取件，在独立`_tmp`输入目录保存，未导出整分区/APEX/所有依赖、未执行ELF。原厂unit config的`com.oplus.camera.jni.version=V002.000.000`与实际cmd JNI相符；不能用一加13目录名猜本机ABI。

## Cmd JNI 与编码桥

以下均为固定ELF VA，不是可直接写入的补丁偏移。

- Cmd JNI使用直接Java长名导出，未见JNI_OnLoad动态注册：createClient `0x78884`、transact `0x78980`、destroyClient `0x78c44`。
- 真正engine为`libAlgoProcess.so`：loadAPSAdapterLib实际dlopen/dlsym `onTransact`，Java transact在`0x78ae4–0x78af4`通过gAPSAdapterOps+8调用；同库注册`setRequestActionCallback`。
- 原callback精确Java目标是APSClient静态`onTransact(Object,int,HashMap,HashMap):int`，不是任意Map接口。JNI仅回写outMap；改camera数组或inputMap不能证明APS encoder接收。
- JNI request-action15进入heifProcess桥；它与Java write-finished事件0x18是两个方向，不能混用。
- HEIF桥`heifProcess@0x21ac`实际start，`0x2254/0x2258`设置`setCallback(nullptr)`，`0x226c`调用processInputFrame，随后close/destroy。仅有callback符号不等于已启用完整容器回调。

## Gainmap辅助接口不等于新拍HEIF元数据

Cmd JNI action23由JavaGainmap生成七个十进制scalar字符串：ratioMin[0]、ratioMax[0]、gamma[0]、epsilonSDR[0]、epsilonHDR[0]、minDisplayRatioForHdrTransition、displayRatioForFullHdr。

engine实际caller为`APSDataUtils::getGainMapInfo@0x2ab0d4`，`0x2ab360`请求23，读取`jni_data_gainmap_info`。ratioMax直接stof，单位为线性值；写到输出GainmapMetadata的RGB三个字段。gamma转为倒数。

当前唯一已核caller是`IPUManager::convertOriginImgFmt@0x176b68`，输出用于`APSUltraHdr::applyGainmap`与P010转换；初始化缺省package为图库。它不是已证明的当前capture HEIF tmap生产者，不能改此helper冒充8×出片。

## JPEG-R 库排除误用

`libapsultrahdr.so`的`encodeJPEGRExternalAPI4@0x3588c`接受压缩base/gain JPEG、metadata与destination四指针，真实链为JpegR::encodeJPEGR→appendGainMap。输出XMP APP1＋MPF APP2，不生成本题62-byte HEIF tmap。

本库metadata确证56字节：24-byte libc++ std::string(version)＋8个float。+0x18 maxContentBoost（linear）、+0x1c min、+0x20 gamma、+0x24/+0x28 offsets、+0x2c/+0x30 capacityMin/Max、+0x34 OplusScale；XMP上限经log2f。它不等于APS不透明`ultrahdrGainMapInfo_BYTE_BASIC_ARRAY_`。

不能因结构含max就强套原blob，也不能仅改min/gamma掩盖相框或生成虚假的8×能力。

## HEIF writer没有可用的完整数组窗口

`liboplusheifwriter.so`实际processInputFrame将数据排队到looper，codec输出逐块ABuffer交MediaMuxer；容器序列化由muxer承担。

- `setCallback@0x1146c`将指针放对象+0x150。
- 候选+0x138/+0x140仅在constructor/start清零、handler读取、destroy条件删除，未见producer或地址逃逸。
- handler `0xce74–0xce80`传给callback的ordinary ptr/len是null/0；回调发生在muxer stop后，硬编码status0不证明所有写入成功。
- ordinary processInputFrame record+0x18=1保留调用者FD，cf18 close仅在state0的不支持memory-FD分支。writer.close主要释放encoder引用，不关闭FD；下游dup/close未闭合。
- livephoto helper在processInputFrame后再追加视频/`gain`/扩展尾部；callback更早，不能据此判最终容器已完整。

因此不能在此callback应用原62byte tmap＋XMP倍率补丁，也不能用同名函数/非零指针代替完整容器、长度和独占契约。

## 引擎中的 JPEG 元数据与真正 HEIF 分支

`APSUltraHdr::uhdrLoadLibrary@0x2c1a18`实际打开本机`libapsultrahdr.so`，`0x2c2478`查找API4并写到全局函数槽`0xa48618`。`waterMarkSrcJpegrCodec@0x2c7648`的`0x2c80ac`向该槽传入base JPEG/gain JPEG/0x38 metadata/destination四参数，跨库ABI已闭合；元数据max/capacityMax/OplusScale被写为常量约4.92610836，不是8×，也不是测量当前HEIF最大值。

`heifCodecProcess@0x6e18ec`条件调用`compressUltraHDRGainMap`、`waterMarkSrcJpegrCodec`和`hwHeifEncodec`。前者只压缩gain图，不传上面的0x38对象；本engine的`hwHeifEncodec@0x6a5f34`为return−1 stub。**stub不代表本机缺HEIF引擎**：另一个重载`0x6e3bd0`实际从参数`jni_custom_data_callback_ptr`取得回调，并在`0x6e49b8`以action18（0x12）交接原图buffer及`heic_src_buf_type_capture/overlay`。谁注册此key、该0xc78记录的完整布局、gain metadata与实际encoder消费者尚未闭合；不能把它等同SDK write-finish0x18、JNIaction15或已经确认的JPEGR字段。

## 系统 muxer 已接受序列化 tmap item

本机`libstagefright.so`有真实的`MPEG4Writer::Track::isGainmapMetaData@0x1467a8`和`isGainmapData@0x1468a0`：接收Track/MediaBufferBase/prefixLen，识别六字节`tmap\\0\\0`或`gmap\\0\\0`前缀，返回长度6。

`Track::threadEntry@0x1427f0`剥前缀后复制已有payload，再递交sample及item offset/size。这证明存在**写出前的序列化item**，不是typed max/gamma生产接口，也不是完整HEIF数组。

进一步已证可引用入口 `MediaMuxer::writeSampleData(const sp<ABuffer>&,size_t,int64_t,uint32_t)@0x1b41cc`：参数x0=Muxer、x1=强引用槽、x2=trackIndex、x3=timeUs、w4=flags，flags0x10设置muxd。其后MediaBuffer构造并持引用，再通过Adapter同步交接。源ABuffer可能共享，不能原位写；可考虑独立clone递交，但实际payload长度/布局、同拍tag与引用生命周期必须另证。

包识别只检查L≥7和`tmap\\0\\0`前缀，不验证62字节内容。剥头是绝对`set_range(6,L−6)`，原offset是否0未知，不能直接把68→62当实际测得。写出前对应虚槽data+0x10、offset+0x20、length+0x28，仍不能强读对象opaque字段。

同轨gain-XMP递交与关联未证；双max/XMP必须同验，不得仅改tmap。此入口是可继续验证的小item机会，**不是已实施的原生8×修复**。

引擎另一HEIF重载的custom callback action18（0x12）不等于JNI通用request-action18：后者已证是`actionCreateBitmap`；JNI也没有`jni_custom_data_callback_ptr`字面注册证据。不能凭数字一样或通用回调注册推断是同一编码链。

## 真实相框 worker 与同名插件区别（2026-11-15）

本轮按真实注册入口只读读取`libAlgoInterface.so`的ELF表、动态符号和若干函数窗口，没有导出45MB整库。找到main材料渲染后调用的gain-mask入口：`watermarkMaskProcess@0x1b11d1c`，UHDR分支先`getUHDRGainmapInfo@0x1b0f6d4`，再`doWatermarkMaskProcess@0x1b101ec`；普通Y8分支只走`getGainmapInfo@0x1b110bc`。这些地址仅对当前C17原件有效，不套用C16。

- `getUHDRGainmapInfo`处理的是增益像素buffer，不是scalar max/gamma/tmap。必要时分配零背景RGB画布、按像素宽×3复制，释放旧buffer/对象及尾vector后替换owner；它忽略源物理stride，不能声称完整metadata保持。
- Y8路径也按width复制，且返回descriptor与新分配buffer的关联未证明，不能当作owned clone或跨调用有效指针。
- virtual+0x58是真getOrientation；真正渲染virtual+0x70进`APSAlgoBase::processImage@0x1808e44`，通过对象成员+0x60调用插件proc(handle,canvas,material,params,extra)。main与gain共用proc，材料backing共享，不能原位改主素材。
- `doWatermarkMaskProcess`仅最多渲染首个合格材料，包含overlap判断，不证明照片ROI被排除。

`doWatermarkRegister@0x1b0be2c`→`loadAlgoAPI@0x180437c`真实加载路径为**`/odm/lib64/libwatermark_photo.so`**、诊断名OCamWaterMark、导出`get_algo_api`。这份插件1,013,712字节，SHA-256 `ac9936084bbfed59fd9e6629602efc29822385262eed3c2ebbf75150d1c4e23e`，不同于相机APK内同名库。

插件`get_algo_api@0x724e8`将Process写入table+0x50，实际Process@0x71dc4调用WaterMark::process@0x72ed8。当前固定插件对RGB gain枚举7和Y8枚举1不绘制；支持的是普通YUV格式。普通单平面buffer还会因第二平面为空被内层拒绝，而外层Process始终返回0。这不能解释174生产点，也不能把跳过mask或返回0当成修复，因此未部署该跳过方案。

真正8×生产/关联XMP、gain装饰原生来源、当前拍摄scope和buffer独占仍未闭合；**C16正常XPAN保留，C16/C17倍率两项都未完成**。

## 2026-11-15 继续测试：系统 HEIF 交接链已闭合

本轮没有安装、启停／重开相机、拍照或重启。只读手机仍为C17 `7.013.51`、原厂C17 SDK、旧诊断APK `dabd7e59…`；以下仍是静态证据，不是同拍命中或8×验收。

- 已有本机engine `custom_data_callback@0x30741c` 对action18通过表项转成transaction7，进入 `ApsCallbackHeifCodec`；`preProcess@0x337d58` 将对象+0x48 descriptor指针作为 `bufferPlanesData_LONG_BASIC_`，附 `heifCodecType_STRING_BASIC_=processHeifEvent`。
- SDK `APSClientCallback.processManipulate` → `ApsHelper.postProcessHeifEvent(J)` → 系统 `android.hardware.camera2.OplusCameraManager.sendToProcessHeif(J)V`，不是原HEIF字节数组的所有权交接。
- 定向读取本机bootclasspath中的`/system/framework/oplus-framework.jar`（12489099字节，SHA-256 `5217c8299d26e47b5f643a9399abf12c9b0711f511fb0ada60d7c4d866a7af7a`）。用已有可信工具只反编译`OplusCameraManager`一个类，确认它加载 `HeifWinBufExchg-jni`，再调用拼写确实为 `nativtSendToProcessHeif(J)V` 的原native成员。
- 定向读取`/system_ext/lib64/libHeifWinBufExchg-jni.so`（4920字节，SHA-256 `633f94c7f4099ca43a717baba0353936ebab00c647f55bbf5bf0d93cfb5908db`）。真实长名导出在0x190c，直接拆解descriptor +0/+4/+8/+c/+10/+14、+18/+20、+28、+30、+38/+3c和+40地址，共13个参数交给已核 `libHeifEncoderWrapper.so` 的 `heifProcess`；没有独立64位完整HEIF数组窗口、没有native注册猜测。
- 进一步小窗核对`processInputFrame@0x10c34`：首指针进入`addYuvBuffer`，第二指针按长度复制后交`analysisExif`／`changeExif`，这是图像＋EXIF输入，不是base／gain pair。`initializeCodec@0x159ac`的已核配置只有尺寸、tile/grid、color及码率等，未见typed gainmap max设置。
- `Encoder::addYuvBuffer@0x12c44`经`dequeueOutputBuffer`／`getOutputBuffer`，在flags恰等于1时复制codec输出bytes到vector；`Writer::onOutputBufferReceived@0xde90`按tile／track、time0和flags1交给muxer。`startMuxer@0xdc78`循环用同一输出AMessage加track，已核仅有BitDepth／is-default，没有独立gain／tmap识别。没有本次真实小包，不能把这条codec复制链直接认作实拍tmap producer；paired gain-XMP和typed max来源仍未知。
- 这补上了系统framework到原wrapper的缺口，但仍未取得typed max→62-byte tmap及同拍gain-XMP的producer、独占／同步和capture关联。writer的`createXmp@0x11478`只是返回1，`mXmpDataHead`是MotionPhoto/video模板，不拿它当gain-XMP证据。**仍不实施opaque pointer改写或仅改单个tmap。**

本地证据：`_tmp/xpan-next-readonly-7xbm4fdz/framework-paths.txt`、`_tmp/framework-heif-class-l_qtnvqr/OplusCameraManager.smali:169–171,1041,2006–2017`、`_tmp/pjx-c17-heif-win-jni-input-5g_x0034/libHeifWinBufExchg-jni.so`。非root maps首次读取收到Permission denied，未更改SELinux或规避限制；随后使用既有授权root只读，`heif-loaded-paths.txt`确认PID30876实际加载micro JNI／wrapper／writer／stagefright。加载路径不证明本轮拍摄命中、descriptor所有权或8×生效。

## 2026-11-15 实拍验证后的新增排除

本轮另行取得真机更新／拍照许可，实际新拍4张。第三张是普通模式，其它3张XPAN仍约4.95656×。只读APS诊断补拍确认`onHeicReceived`原入口即`mCopyBuffer=null`、`bufferType=17`、`mode=xpan`、`mbHeifProcessInAps=true`，不再假设只是在c0后变空。诊断默认关闭，补拍后已关闭；详见[当前包实机验收](C17当前包实机验收与调试.md)。

- `resultinfo_heif_encode_in_aps`在本机engine `covertResultInfo2Vector`的0x2ebd7c读取结果对象+0x7c导出布尔文本，未闭合成编码位置控制键。
- 原生`ultrahdrGainMapInfo`数组转出适配已定向定位：`AlgoInterface::setExtenderFile@0x363674`取现有ptr并写`uhdr_info_addr`，把`uhdr_info_size`写成0x50；capture回调再转Java数组。80字节只是已证输出长度，typed字段／原始producer／上限单位仍未知，不按0x48公开UHDR结构强套。
- 系统`/system/lib64/libultrahdr.so`，308352字节、SHA-256 `c71c134b07cd7fd1984117cf2d09643ef3adbe90006eca623079df939b4bcb7b`，实际已加载但编码输出是JPEG-R。`uhdr_enc_set_gainmap_image`处理0x48公共metadata，不能据此当作当前HEIF tmap producer。
- `liboplussfplugin_ccodec.so`，256040字节、SHA-256 `e64fd6f5746c5f74e26e56d8070773d29c89f280ec7f40fd7e0b9f755a9ec14b`，已核`configureTonemapFilter`只是`vendor.oplus.tonemap.filter.request-enable/package-name`及color-transfer-request，`setHdrType`只是HDR分类；没有同拍gain-max／tmap-XMP生产证据，不把改tone-map字段当8×修复。
- 标准相框JSON只有共同背景色／TOP-BOTTOM材料参数，没有已证gain-only颜色；改共同参数会触碰主图框色，不能为消除174而改主水印或全局frame input。

以上仅静态补缺，不新增生产native payload。下一轮原生只读注入尚无明确回答，未执行；任何小包修改仍需同拍关联／typed producer及配套XMP证据，不仅凭符号／结果flag／固定长度放行。

## 实际落地修改

- `XpanGainMapFrame.inputEvidence`按每个数值键独立报告：缺format只记format=absent，不再吞掉gain尺寸/stage。
- 新增同一次w内的原e自然分支/输出shape只读观察：严格关联holder/tag/参数与Gate，资格与参数修复完全分离，不打印LUT/指针/blob/照片/整JSON。
- 原e合法修改共享素材数组不能让诊断误判失去关联；after-e采用对象/几何关联，classifier及真正参数修改仍保持完整snapshot门槛。
- 首次诊断回归出现“natural e output missing”，已定位到素材snapshot门槛过严，保留失败记录并修正后重跑；不删除失败行为项，不扩大原v4/Y8/无框变更门槛。

本轮尚未安全接入新的非overlay修复或native8×入口。后续已按用户授权安装逐键诊断`dabd7e59…`并补拍1张，仍未通过8×。没有据原生符号/零返回值写成生效，也没有新增未授权安装或旧文件写回。
