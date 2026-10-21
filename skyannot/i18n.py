"""Interface strings. Chinese is the source; English translations below. Language follows the system unless
set in the settings."""
from __future__ import annotations

import locale

_EN = {
    "TTSkyAnnot · 星图标注": "TTSkyAnnot · star chart annotator",
    "打开": "Open",
    "上一张": "Previous",
    "下一张": "Next",
    "播放": "Play",
    "暂停": "Pause",
    "图像操作栏": "Image action bar",
    "显示或隐藏画面底部的操作栏（上一张/播放/下一张/删除/移动）":
        "Show or hide the bar along the bottom of the picture (previous / play / next / remove / move)",
    "删除当前图像（可撤销）": "Remove this image (can be undone)",
    "把当前图像移动到…": "Move this image to…",
    "移动到文件夹": "Move to folder",
    "已移入回收站": "Moved to the Recycle Bin",
    "已删除（回收站不可用，无法撤销）": "Deleted (no Recycle Bin on this drive; cannot be undone)",
    "已永久删除": "Deleted for good",
    "已移动": "Moved",
    "已撤销删除": "Delete undone",
    "已撤销移动": "Move undone",
    "撤销删除或移动": "Undo remove or move",
    "按 Ctrl+Z 撤销": "press Ctrl+Z to undo",
    "回收站里已经找不到它了": "It is no longer in the Recycle Bin",
    "撤销失败": "Could not undo",
    "无法删除这个文件": "Could not remove this file",
    "无法移动这个文件": "Could not move this file",
    "这个文件夹里没有图像了": "No images left in this folder",
    "自动播放当前目录下的所有图片": "Play through every image in this folder",
    "每隔几秒自动切换到下一张": "Automatically move to the next image every few seconds",
    "自动播放间隔": "Playback interval",
    " 秒": " s",
    "适应窗口": "Fit",
    "原始大小": "1:1",
    "拉伸": "Stretch",
    "自动": "Auto",
    "联动": "Linked",
    "分通道": "Unlinked",
    "不拉伸": "None",
    "解析并标注": "Solve && annotate",
    "保存当前结果": "Save this result",
    "保存结果": "Save results",
    "把这张图的解析结果写到磁盘（解析本身不写任何文件）":
        "Write this image's results to disk (solving on its own writes nothing)",
    "还没保存的结果": "Results not saved yet",
    "张": "image(s)",
    "保存解析结果": "Save the solution",
    "解析完成": "Solved",
    "不勾就不会写任何文件；「写回图片」会修改原文件的文件头（像素不动）":
        "Nothing is written for what is left unticked; \"write into the image\" changes the original file's "
        "header (pixels are untouched)",
    "以后都按这次的选择保存": "Save this way from now on",
    "批量处理时不再逐个询问": "Stops the question coming up again for every image in a batch",
    "取消处理": "Cancel the run",
    "保存失败": "Could not save",
    "在 PixInsight 中打开": "Open in PixInsight",
    "用 PixInsight 打开当前显示的原图（XISF/FITS 保留文件头，不做任何改动）":
        "Open the original file on screen with PixInsight (XISF/FITS keep their header; nothing is changed)",
    "PixInsight 路径(留空 = 自动查找)": "PixInsight path (empty = find it automatically)",
    "无法打开 PixInsight": "Could not start PixInsight",
    "显示标注": "Show annotations",
    "文件头": "Header",
    "检查更新": "Check for updates",
    "从「设置 → 更新源」检查是否有新版本": "Check the update source set in Settings for a newer build",
    "还没有设置更新源。请在「设置 → 更新源」里填 version.json 所在的地址或文件夹。":
        "No update source is set. Put the address or folder that holds version.json in Settings → Update source.",
    "检查中…": "Checking…",
    "检查更新失败": "Update check failed",
    "新版本": "new version",
    "已是最新": "Up to date",
    "已是最新版本": "You are on the latest version",
    "有新版本": "New version available",
    "已跳过": "skipped",
    "更新源(留空 = 关闭)": "Update source (empty = off)",
    "更新源：放着 version.json 的地址、网络共享或文件夹。留空则启动时不检查更新，仍可用状态栏的「检查更新」手动查。":
        "Where version.json lives: a URL, a network share or a folder. Left empty, the program does not check "
        "on startup; the status bar's Check for updates still works.",
    "软件更新": "Software update",
    "当前版本": "current version",
    "下载大小": "download size",
    "跳过这个版本": "Skip this version",
    "下次启动不再提示这个版本；仍可用状态栏的「检查更新」手动检查":
        "Do not offer this version again on startup; the status bar's check still finds it",
    "稍后": "Later",
    "立即更新": "Update now",
    "只有打包后的程序才能替换自己。": "Only the packaged program can replace itself.",
    "只有打包后的程序才能自动更新。": "Only the packaged program can update itself.",
    "更新失败": "Update failed",
    "无法开始更新": "Could not start the update",
    "修正文件头": "Fix header",
    "焦距": "Focal length",
    "镜头的实际焦距；解析器用它算像素尺度和视场":
        "The real focal length of the optics; the solvers use it (with the pixel size) to know the field size",
    "像素尺寸": "Pixel size",
    "传感器像元尺寸（含 Bin）": "Sensor pixel size, binning included",
    "画面中心": "Frame centre",
    "像素尺度": "pixel scale",
    "视场": "field",
    "写入文件": "Write to file",
    "无法写入文件头": "Could not write the header",
    "已修正文件头": "header updated",
    "应用到整个文件夹": "Apply to the whole folder",
    "把同样的值写进这个文件夹里的每一张 FITS":
        "Write the same values into every FITS frame in this folder",
    "（共 %d 张 FITS）": "(%d FITS frames)",
    "（这个文件夹里只有这一张）": "(the only frame in this folder)",
    "正在写入文件头": "Writing headers",
    "有 %d 张写不进去（见日志）": "%d could not be written (see the log)",
    "已修正 %d 张的文件头": "Header corrected on %d frames",
    "失败 %d 张": "%d failed",
    "只有 FITS 文件能这样修改文件头。": "Only FITS files can be corrected this way.",
    "设置": "Settings",
    "标注": "Annotate",
    "目标名(可选)": "Target name (optional)",
    "天体名": "Object",
    "天体名(可选，可搜索)": "Object (optional, searchable)",
    "输入 M57、NGC6720、Ring、6720 都能找到；选中后用它在天球上的准确位置做解析提示，比文件头里的指向更可靠":
        "M57, NGC6720, Ring or 6720 all find it; picking one uses its true sky position as the solve hint, "
        "which is more reliable than the header's pointing",
    "使用天体": "using object",
    "中心坐标": "Centre RA/Dec",
    "直接输入画面中心的赤经/赤纬（时 分 秒、度 分 秒），优先于天体名用作解析提示；例：05 34 31.9 +22 00 52，也接受 05:34:31.9 +22:00:52":
        "The RA/Dec of the frame's centre, written as hours/minutes/seconds and degrees/minutes/seconds. It "
        "is used as the solve hint in preference to the object field. Example: 05 34 31.9 +22 00 52 — "
        "05:34:31.9 +22:00:52 works too.",
    "坐标无法识别（时 分 秒 / 度 分 秒）：": "Not a position (hours/minutes/seconds, degrees/minutes/seconds):",
    "使用画面中心坐标": "using the centre position",
    "解析提示：画面中心": "solve hint: frame centre",
    "在线天体库(留空 = 关闭)": "Online object catalogue (empty = off)",
    "在线天体库地址。填了它，天体名输入框会在本地星表之外再查这个库（LBN/LDN 等本地没有的编号都在那里）；留空则只查本地星表。":
        "Address of the online catalogue. With it set, the object field also searches there — the Lynds and "
        "other deep catalogues the bundled files do not have. Left empty, only the bundled catalogues are "
        "searched.",
    "在线星表补充": "online catalogue added",
    "个天体": "object(s)",
    "在线星表": "online catalogue",
    "例如 M31。没有文件头的 JPG/PNG 盲解失败时填它": "e.g. M31. Helps JPG/PNG without a header when blind solving fails",
    "输出": "Outputs",
    "天体列表 TXT": "Object list TXT",
    "标注图": "Annotated image",
    "轮廓坐标文件": "Outline coordinates (JSON)",
    "解析结果写回图片": "Write solution into the image",
    "开始": "Start",
    "打开输出文件夹": "Open output folder",
    "把图片拖到这里,或点「打开」": "Drop images here, or click Open",
    "文件夹": "Folder",
    "同目录下的图片": "Images in this folder",
    "显示或隐藏左侧的同目录图片列表": "Show or hide the list of images in this folder",
    "在文件资源管理器中显示": "Show in File Explorer",
    "复制文件完整路径": "Copy full file path",
    "复制所在文件夹路径": "Copy folder path",
    "上一张（←）": "Previous (←)",
    "下一张（→）": "Next (→)",
    "正在读取…": "Loading…",
    "读取失败": "Could not open the file",
    "未解析": "not solved",
    "已解析": "solved",
    "已拉伸": "already stretched",
    "下载完成后程序会关闭，随后打开安装程序，按向导完成更新。": "The program will close when the download finishes, then the installer opens: follow its wizard.",
    "只有打包后的程序才能替换自己。": "Only a packaged build can replace itself.",
    "拖入图片，或点这里打开": "Drop an image here, or click to open",
    "支持 FITS / XISF / TIFF / JPG / PNG": "FITS / XISF / TIFF / JPG / PNG supported",
    "ttsky_…（点「领取密钥」免费获取）": "ttsky_… (click Get a key, it is free)",
    "领取密钥…": "Get a key…",
    "在浏览器中打开领取页面（免费）": "Opens the key page in your browser (free)",
    "在线解析密钥": "Online solve key",
    "在线解析需要一个免费密钥": "Online solving needs a free key",
    "匿名额度很小（每天 20 次）。用一个免费密钥可以解析到每小时 150 次、每天 600 次。": "The anonymous allowance is small (20 a day). A free key raises it to 150 an hour and 600 a day.",
    "密钥在网页上自助领取，填回「设置 → 在线解析密钥」即可。": "Get one on the web page and paste it into Settings → Online solve key.",
    "打开领取页面": "Open the key page",
    "这张图还没有解析：标注来自解析结果，先用「解析并标注」把它解出来": "This frame is not solved yet: annotations come from the solution, so solve it first with 「Solve and annotate」",
    "解析失败：": "Solve failed: ",
    "可以在「天体名」里填目标（如 M33），或在「中心坐标」里填画面中心，解析器会快很多。": "Filling in the target name (e.g. M33) or the centre coordinates helps the solver a lot.",
    "完成": "Done",
    "失败": "Failed",
    "处理中…": "Working…",
    "关键字": "Keyword",
    "值": "Value",
    "说明": "Comment",
    "nova.astrometry.net API key(可选,最后的兜底)": "nova.astrometry.net API key (optional, last resort)",
    "ASTAP 路径(可选,离线解析)": "ASTAP path (optional, offline solving)",
    "Gaia 星等上限": "Gaia magnitude limit",
    "输出位置(留空 = 图片所在文件夹)": "Output folder (empty = next to the image)",
    "界面语言": "Language",
    "界面配色": "Colour theme",
    "深色": "Dark",
    "浅色": "Light",
    "Messier": "Messier",
    "NGC": "NGC",
    "IC": "IC",
    "Sharpless": "Sharpless",
    "VdB": "VdB",
    "Ced": "Ced",
    "RCW": "RCW",
    "Messier 星表（OpenNGC）": "Messier catalog (OpenNGC)",
    "NGC 星表（OpenNGC）": "NGC catalog (OpenNGC)",
    "IC 星表（OpenNGC）": "IC catalog (OpenNGC)",
    "Sharpless HII 区星表（Sh2-）": "Catalog of HII regions (Sharpless, Sh2-)",
    "Lynds 亮星云（在线获取）": "Lynds bright nebulae (fetched online)",
    "Lynds 暗星云（在线获取）": "Lynds dark nebulae (fetched online)",
    "van den Bergh 反射星云（在线获取）": "van den Bergh reflection nebulae (fetched online)",
    "Cederblad 发射星云（在线获取）": "Cederblad emission nebulae (fetched online)",
    "RCW 发射星云（在线获取）": "RCW emission nebulae (fetched online)",
    "和星表无关，单独开关": "Not a catalogue: switched on its own",
    "和星表无关，单独开关；默认开启": "Not a catalogue: a switch of its own, on by default",
    "删除当前图像（会先确认，删后可撤销）": "Delete this image (asks first; undoable afterwards)",
    "删除图像": "Delete image",
    "无法在文件资源管理器中定位": "could not locate this in the file manager:",
    "要删除这一张吗？": "Delete this frame?",
    "移入回收站": "Move to Recycle Bin",
    "删除确认": "Delete confirmation",
    "删除图像前先确认": "Ask before deleting an image",
    "关掉后按删除键就直接移入回收站（仍可撤销）":
        "With this off, Delete sends the frame straight to the Recycle Bin (still undoable)",
    "彩色": "colour",
    "去底色": "Sky-neutral",
    "自动白平衡": "Auto white balance",
    "直方图": "Histogram",
    "自动拉伸": "Auto stretch",
    "重置拉伸": "Reset stretch",
    "统计数据": "Statistics",
    "关闭": "Close",
    "放大镜：在直方图上拖一个框放大；滚轮缩放，拖动平移，双击复位":
        "Magnifier: drag a box on the histogram to zoom; the wheel zooms, a drag pans, a double click resets",
    "滚轮缩放 · 拖动平移 · 双击复位；拖动黑点／白点／中间调改变拉伸":
        "Wheel zooms · drag pans · double click resets; drag the black / white / midtone handles to restretch",
    "黑点": "black",
    "中间调": "midtone",
    "白点": "white",
    "单色": "mono",
    "已解拜耳": "debayered",
    "解拜耳": "Debayering",
    "已有解": "Existing solution",
    "复用文件头里已有的解": "Reuse the solution already in the header",
    "文件里已经带天体测量解时（ASIAIR/NINA/PixInsight 都会写），先和画面里的星点核对，对得上就直接用，省掉整轮解析。":
        "When the file already carries an astrometric solution — the ASIAIR, NINA and PixInsight all write one "
        "— check it against the stars in the picture and use it when it lines up, saving the whole solve wait.",
    "拜耳阵列": "Bayer pattern",
    "自动（按文件头）": "Automatic (from the header)",
    "RGGB（红 绿 / 绿 蓝）": "RGGB (red green / green blue)",
    "BGGR（蓝 绿 / 绿 红）": "BGGR (blue green / green red)",
    "GRBG（绿 红 / 蓝 绿）": "GRBG (green red / blue green)",
    "GBRG（绿 蓝 / 红 绿）": "GBRG (green blue / red green)",
    "彩色相机的未解拜耳帧如果文件头没写拜耳排列，就在这里指定。\n只对单通道的彩色马赛克帧生效；已经解拜耳或本来就是单色的帧不受影响。":
        "For a one-shot colour frame whose header does not say which Bayer arrangement it used. It applies "
        "only to a single-channel colour mosaic; a frame already debayered, or a monochrome one, is not "
        "affected.",
    "当前图像不是未解拜耳的彩色马赛克帧，这个选项对它无效。":
        "The current image is not an un-debayered colour mosaic, so this does not apply to it.",
    "这张是单通道图像、文件头也没写拜耳排列。如果它其实是彩色相机的未解拜耳帧，在这里指定排列即可解出彩色。":
        "This is a single-channel image whose header names no Bayer arrangement. If it is in fact an "
        "un-debayered one-shot colour frame, pick the arrangement here to bring the colour out.",
    "自动解拜耳单次彩色图像": "Debayer one-shot colour frames automatically",
    "单次彩色相机的原始帧是单通道的马赛克；开启后自动插值成彩色再显示。关掉可查看原始马赛克数据。":
        "A one-shot colour camera writes a single-channel mosaic; with this on it is interpolated to colour "
        "before being shown. Turn it off to look at the raw mosaic data.",
    "选中后立即生效，无需重启": "Applies immediately; no restart needed",
    "界面语言已保存，需要重启后生效。现在重启吗？":
        "The interface language is saved and needs a restart. Restart now?",
    "无法自动重启，请手动重新打开程序。": "Could not restart automatically; please open the program again by hand.",
    "设置文件写入失败：%s\n配色等选项可能在下次启动时不生效。":
        "Could not write the settings file: %s\nThe theme and other options may not survive a restart.",
    "跟随系统": "System",
    "确定": "OK",
    "取消": "Cancel",
    "浏览…": "Browse…",
    "写回会修改原文件的文件头(像素不动)。FITS/XISF 写进文件头,JPG/PNG/TIFF 另存同名 .wcs 文件。":
        "Writing back changes the original file's header (pixels are untouched). FITS/XISF get header keywords, "
        "JPG/PNG/TIFF get a sidecar .wcs file.",
    "精度": "accuracy",
    "颗星配准": "stars matched",
    "像素": "px",
    "关于": "About",
    "标注内容": "Show",
    "打开历史": "History",
    "打开过的图片会按顺序记在这里，之后可以一起解析；在左栏点选或翻页看过的图也会自动加进来":
        "Images you open are kept here, in order, for a later batch run; anything opened from the folder "
        "list or stepped to is added automatically",
    "PixInsight 格式的天体列表，存在图片旁边": "The object list in PixInsight's format, written beside the image",
    "带标注的预览图，另存为 *_annotated.jpg": "A preview with the annotations drawn on, saved as *_annotated.jpg",
    "星系": "Galaxies",
    "星云": "Nebulae",
    "星团": "Clusters",
    "暗星云": "Dark nebulae",
    "颜色按天体类型：同一类型里的每个天体再取属于自己的深浅": "Colours follow the kind of object; inside a family each object takes a shade of its own",
    "其他 NGC/IC 条目": "Other NGC/IC entries",
    "亮星名": "Star names",
    "坐标网格": "RA/Dec grid",
    "天体形状": "Object shapes",
    "星表": "Catalogues",
    "全选": "All",
    "清空": "None",
    "全部解析": "Solve with every catalogue",
    "一个都不解析": "Solve without any of them",
    "亮星表（NamedStars）始终写出": "The bundled NamedStars list is always written",
    "解析后写出 %s 星表（不勾就不查询、不写出）":
        "Write the %s catalogue (when unchecked it is not queried and not written)",
    "至少要选一个星表": "Choose at least one catalogue",
    "轮廓线": "Outlines",
    "圆圈": "Circles",
    "轮廓线：有轮廓数据的星云按实际形状描边。圆圈：一律按星表尺寸画圆，长条形天体画成椭圆。":
        "Outlines: nebulae with outline data are traced along their shape. Circles: every object is drawn at "
        "its catalogue size, elongated ones as ellipses.",
}

_lang = None


def set_language(code: str) -> None:
    global _lang
    _lang = code or None


def language() -> str:
    if _lang:
        return _lang
    try:
        loc = (locale.getlocale()[0] or locale.getdefaultlocale()[0] or "").lower()
    except Exception:
        loc = ""
    return "zh" if loc.startswith(("zh", "chinese")) else "en"


def t(s: str) -> str:
    return _EN.get(s, s) if language() == "en" else s


