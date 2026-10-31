# 预研：XPAN 快门声

## Muse 机制（X9U）
X9U 相机里 GR 快门音资源是 0 字节文件，`SoundPool.load` 永远加载不完、播放请求挂队列。
钩子（hook/gr/GrShutterSound.java）在 load 遇到这几个空资源时改从模块 assets 加载
realme 相机（RMX5200）的原版音效。

## Ace 3 Pro（6.020.876）核对
- 本机 assets/音效文件名已混淆（`11_反编译/res/*.ogg`，如 -8.ogg、0E.ogg）。
- `tools/arsc_dump.py` 按 "shutter"/"xpan" 反查 resources.arsc 超时/无命中——资源名混淆。
- 一加 12 相机 7.006.107 提取（`03_对比分析/apk_7.006.107/extracted/res/`）同样混淆命名。

## 结论与路径
手法可移植（hook `SoundPool.load` 换音源），**卡在音源资源定位**：
1. 在 `11_反编译` 里找 X-Pan 显影/快门代码对 R.raw 的引用（X-Pan 代码在
   `smali_classes18`（bh/c、dh/w 一带），找 SoundPool/Xylophone/MediaPlayer 用法）；
2. 由资源 ID 经 `tools/arsc_dump.py`（需要优化速度或改用 androguard）映射到混淆文件名；
3. 音源若与一加 12 相机一致，直接取 `03_对比分析/apk_7.006.107/extracted/res/` 对应文件。
状态：**待实机/待深挖**——X-Pan 本身用的是移植模式的快门声，此项为"锦上添花"，优先级低。
