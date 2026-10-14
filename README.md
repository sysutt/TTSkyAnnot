# TTSkyAnnot · 星图查看与标注

一个独立的小工具：快速查看天文图像，自动解析星场，标注深空天体和恒星，不需要 PixInsight。

A small standalone tool to view astronomical images, plate-solve them and annotate deep-sky objects and stars,
without PixInsight. English summary below.

## 能做什么

**查看**
- 打开 FITS、XISF、TIFF、JPEG、PNG。可以直接拖进窗口，也可以在资源管理器里右键用「打开方式」选择 TTSkyAnnot。
- 线性数据（叠加母版）按 PixInsight 的自动屏幕拉伸（STF）显示，可切换「联动」「分通道」「不拉伸」。
- 滚轮缩放，拖动平移，「F」适应窗口，「1」原始大小，左右方向键切换同一文件夹里的上一张、下一张。
- 鼠标处显示像素原始数值；已解析的图同时显示赤经、赤纬。
- 「H」查看文件头：FITS 关键字和 XISF 属性。
- 已经带解析结果的图，打开就叠加显示深空天体和亮星，不联网、不等待。

**解析与标注**（「解析并标注」，可以一次选多张）
1. 本机提取星点 → 在线粗解（只发送星点坐标）→ 用 Gaia DR3 精修，带畸变修正（TAN-SIP）。
   在线粗解不成功时，依次改用 ASTAP（需自行安装）和 nova.astrometry.net（需在设置里填 API key）。
2. 输出：
   - `<图名>.objects.txt`：和 PixInsight AnnotateImage「Write objects to a text file」格式一致，包括亮星名、Messier、NGC-IC、Sharpless、Hipparcos、Tycho-2、Gaia DR3（默认 16 等以内）；
   - `<图名>.annotated.jpg`：标注预览图；
   - `<图名>.outlines.json`：每个天体轮廓的像素坐标；
   - 解析结果写回原图：FITS/XISF 写进文件头，像素和其他内容原样不动；JPG/PNG/TIFF 另存同名 `.wcs` 文件。

**精度**：以图上实测的星点质心为准，在拍摄历元下比较。

| 图像 | PixInsight | TTSkyAnnot |
|---|---|---|
| M33，每像素 1.08″ | 中位 0.23 px | 中位 0.13 px |
| M1，每像素 0.29″ | 中位 0.35 px | 中位 0.35 px |

复测方法见 `tests/accuracy_vs_pi.py`。

**和 PixInsight 导出的 TXT 有哪些不同**

- **像素坐标按拍摄日期的星位计算**，标出的是照片里星星实际所在的位置；PixInsight 投影的是 J2000 星位，自行大的星会偏开，在每像素 0.3″ 的图上约 1 像素。赤经、赤纬两列和 PixInsight 一样，仍写 J2000。
- **NGC/IC 用 OpenNGC 的现代坐标**，比 PixInsight 用的 NGC 2000.0 更准，所以有些天体位置、星等、直径会和 PixInsight 不同。
- **Gaia 块的 Flags 列留空**：那是 PixInsight 本地数据库的内部标志，VizieR 上没有。
- **亮星块**用耶鲁亮星表加 IAU 正式星名，不含 PixInsight 那些非正式俗名。

## 运行

```bash
pip install -r requirements.txt
python -m skyannot                       # 窗口
python -m skyannot image.xisf            # 打开指定图片
python -m skyannot --annotate *.fits     # 命令行批量处理,不开窗口
```

Windows 版打包：`packaging\build.bat`，生成 `dist\TTSkyAnnot\`，里面有窗口版 `TTSkyAnnot.exe` 和命令行版
`ttskyannot-cli.exe`。

## English

- **View** FITS, XISF, TIFF, JPEG and PNG: automatic screen stretch for linear data (linked / per channel),
  zoom and pan, raw pixel values and RA/Dec under the cursor, header table, previous/next image in the folder,
  and instant deep-sky overlay for images that already carry a solution.
- **Solve and annotate**: local star detection, online rough solve (only star coordinates are sent; ASTAP and
  nova.astrometry.net as fallbacks), then a TAN-SIP refinement against Gaia DR3 that matches PixInsight's
  accuracy (table above).
- **Outputs**: a PixInsight AnnotateImage-compatible object list, an annotated preview, outline polygons in
  pixel coordinates (JSON), and the solution written back into the FITS/XISF header (pixels untouched) or a
  sidecar `.wcs` file.
- Pixel positions use the star positions at the observation date, so they match the picture; the RA/Dec
  columns stay J2000 like PixInsight's.

## Licence

GPL-3.0-or-later, see `LICENSE`. Bundled data and libraries are listed with their licences in
`THIRD_PARTY_NOTICES.md`. Deep-sky data: OpenNGC (CC BY-SA 4.0); nebula outlines: OpenNGC and Stellarium.
