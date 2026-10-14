"""Interface strings. Chinese is the source; English translations below. Language follows the system unless
set in the settings."""
from __future__ import annotations

import locale

_EN = {
    "TTSkyAnnot · 星图查看与标注": "TTSkyAnnot · astro image viewer and annotator",
    "打开": "Open",
    "上一张": "Previous",
    "下一张": "Next",
    "适应窗口": "Fit",
    "原始大小": "1:1",
    "拉伸": "Stretch",
    "自动": "Auto",
    "联动": "Linked",
    "分通道": "Unlinked",
    "不拉伸": "None",
    "解析并标注": "Solve && annotate",
    "显示标注": "Show annotations",
    "文件头": "Header",
    "设置": "Settings",
    "标注": "Annotate",
    "目标名(可选)": "Target name (optional)",
    "例如 M31。没有文件头的 JPG/PNG 盲解失败时填它": "e.g. M31. Helps JPG/PNG without a header when blind solving fails",
    "输出": "Outputs",
    "天体列表 TXT(PixInsight 格式)": "Object list TXT (PixInsight format)",
    "标注预览图": "Annotated preview",
    "轮廓坐标文件": "Outline coordinates (JSON)",
    "解析结果写回图片": "Write solution into the image",
    "开始": "Start",
    "打开输出文件夹": "Open output folder",
    "把图片拖到这里,或点「打开」": "Drop images here, or click Open",
    "正在读取…": "Loading…",
    "读取失败": "Could not open the file",
    "未解析": "not solved",
    "已解析": "solved",
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
    "星系": "Galaxies",
    "星云": "Nebulae",
    "星团": "Clusters",
    "暗星云": "Dark nebulae",
    "其他 NGC/IC 条目": "Other NGC/IC entries",
    "亮星名": "Star names",
    "坐标网格": "RA/Dec grid",
    "天体形状": "Object shapes",
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
