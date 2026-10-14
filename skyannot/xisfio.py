"""XISF 1.0 file reading/writing, written from PixInsight's public format specification
(https://pixinsight.com/doc/docs/XISF-1.0-spec/XISF-1.0-spec.html).

Shared with TTAstroPiLot (same author). The interface mirrors the PyPI `xisf` package:
    XISF(path).read_image(n)          -> ndarray (height, width, channels); mono is (h, w, 1)
    XISF(path).get_images_metadata()  -> [dict per image]: id / geometry (w, h, c) / sampleFormat / bounds /
                                         colorSpace / location / dtype / FITSKeywords / XISFProperties
    XISF(path).get_file_metadata()    -> {property id: {id, type, value}} (file-level <Metadata>)
    XISF.write(path, array, creator_app=, image_metadata=, xisf_metadata=)
Reading: multiple images per file, Planar/Normal storage, UInt8/16/32 and Float32/64, both byte orders,
zlib/lz4/lz4hc/zstd compression (with +sh byte shuffling and subblocks), scalar/string/time/vector/matrix
properties stored inline, embedded or as attachments.
Writing: uncompressed, planar, 4096-byte block alignment (PixInsight's own layout). Boolean properties are
written as "true"/"false" (the PyPI package writes "True" and reads it back as False).
Lossless header edits (keep pixels and attachments byte for byte) live in xisfedit.py.
"""
from __future__ import annotations

import base64
import datetime
import os
import platform
import re
import struct
import sys
import xml.etree.ElementTree as ET
import zlib

import numpy as np

_NS = "http://www.pixinsight.com/xisf"
_Q = "{%s}" % _NS
_SIGNATURE = b"XISF0100"
_ALIGN = 4096                 # 数据块对齐(与 PixInsight 默认一致)
_MAX_INLINE = 3072            # 不超过这个字节数的属性数据直接 base64 写进文件头

# 采样格式 ↔ numpy(规范定义的都是小端,除非 byteOrder="big")
_SAMPLE_FORMATS = {
    "UInt8": np.dtype("uint8"), "UInt16": np.dtype("<u2"), "UInt32": np.dtype("<u4"),
    "Float32": np.dtype("<f4"), "Float64": np.dtype("<f8"),
    "Complex32": np.dtype("<c8"), "Complex64": np.dtype("<c16"),
}
_DTYPE_TO_FORMAT = {np.dtype("uint8"): "UInt8", np.dtype("uint16"): "UInt16", np.dtype("uint32"): "UInt32",
                    np.dtype("float32"): "Float32", np.dtype("float64"): "Float64"}

# 标量属性类型 → Python 值
# 描述像素→天球映射的 FITS 关键字(图像尺寸变了就失效)
_WCS_KEY = re.compile(r"^(CTYPE\d|CRVAL\d|CRPIX\d|CDELT\d|CROTA\d|CUNIT\d|CD\d_\d|PC\d_\d|PV\d_\d+|"
                      r"A_|B_|AP_|BP_|LONPOLE|LATPOLE|PLTSOLVD|WCSAXES)")
_INT_TYPES = {"Int8", "Int16", "Int32", "Int64", "UInt8", "UInt16", "UInt32", "UInt64"}
_FLOAT_TYPES = {"Float32", "Float64"}
_COMPLEX_TYPES = {"Complex32", "Complex64"}
# 向量/矩阵属性的元素类型(前缀 + "Vector" / "Matrix")
_ELEM = {"I8": "i1", "UI8": "u1", "Byte": "u1", "I16": "<i2", "UI16": "<u2", "I32": "<i4", "UI32": "<u4",
         "I64": "<i8", "UI64": "<u8", "F32": "<f4", "F64": "<f8", "C32": "<c8", "C64": "<c16"}

# ───────────────────────────── 压缩 / 字节重排 ─────────────────────────────
def _decompress(codec: str, data: bytes, usize: int) -> bytes:
    if codec == "zlib":
        return zlib.decompress(data)
    if codec in ("lz4", "lz4hc"):
        import lz4.block
        return lz4.block.decompress(data, uncompressed_size=usize)
    if codec == "zstd":
        try:
            from compression import zstd            # Python 3.14+ 标准库
            return zstd.decompress(data)
        except ImportError:
            import zstandard                          # 旧版 Python 的退路
            return zstandard.ZstdDecompressor().decompress(data, max_output_size=usize)
    raise ValueError("XISF:不支持的压缩编码 %r" % codec)


def _unshuffle(data: bytes, itemsize: int) -> bytes:
    """字节重排的逆变换:重排后是「所有元素的第 0 字节、所有元素的第 1 字节…」,尾部不足一个元素的原样。"""
    if itemsize <= 1:
        return data
    a = np.frombuffer(data, np.uint8)
    m = a.size // itemsize
    body = a[:m * itemsize].reshape(itemsize, m).T.reshape(-1)
    return body.tobytes() + a[m * itemsize:].tobytes()


def _block_bytes(fh, elem: ET.Element, location: str, text: str | None) -> bytes:
    """按 location 取出一个数据块的原始字节(已解压、已还原重排)。"""
    parts = location.split(":")
    kind = parts[0]
    if kind == "attachment":
        pos, size = int(parts[1]), int(parts[2])
        fh.seek(pos)
        raw = fh.read(size)
        if len(raw) != size:
            raise ValueError("XISF:数据块越过文件末尾(位置 %d,大小 %d)" % (pos, size))
    elif kind == "inline":
        raw = _decode_text(parts[1], text or "")
    elif kind == "embedded":
        d = elem.find(_Q + "Data")
        if d is None:
            raise ValueError("XISF:location=embedded 但没有 <Data> 子元素")
        raw = _decode_text(d.get("encoding", "base64"), d.text or "")
    else:
        raise ValueError("XISF:不支持的数据位置 %r" % location)
    comp = elem.get("compression")
    if not comp:
        return raw
    cparts = comp.split(":")
    codec = cparts[0]
    shuffle = codec.endswith("+sh")
    if shuffle:
        codec = codec[:-3]
    usize = int(cparts[1])
    itemsize = int(cparts[2]) if (shuffle and len(cparts) > 2) else 1
    sub = elem.get("subblocks")
    if sub:
        out, off = [], 0
        for s in sub.split(":"):
            cs, us = (int(v) for v in s.split(","))
            out.append(_decompress(codec, raw[off:off + cs], us))
            off += cs
        data = b"".join(out)
    else:
        data = _decompress(codec, raw, usize)
    if shuffle:
        data = _unshuffle(data, itemsize)
    return data


def _decode_text(encoding: str, text: str) -> bytes:
    t = "".join(text.split())
    if encoding == "base64":
        return base64.b64decode(t)
    if encoding in ("hex", "base16"):
        return bytes.fromhex(t)
    raise ValueError("XISF:不支持的文本编码 %r" % encoding)


# ───────────────────────────── 属性解析 ─────────────────────────────
def _location_value(loc: str):
    parts = loc.split(":")
    if parts[0] == "attachment":
        return ("attachment", int(parts[1]), int(parts[2]))
    return parts


def _parse_scalar(ptype: str, s: str):
    if ptype == "Boolean":
        return s.strip().lower() in ("true", "1")
    if ptype in _INT_TYPES:
        return int(s)
    if ptype in _FLOAT_TYPES:
        # 整数字面量(如 Observation:Equinox="2000")原包返回 int,写回时也就还是 "2000";照此保持
        t = s.strip()
        return int(t) if t.lstrip("+-").isdigit() else float(t)
    if ptype in _COMPLEX_TYPES:
        re_, im_ = s.strip().strip("()").split(",")
        return complex(float(re_), float(im_))
    return s


def _parse_property(fh, p: ET.Element) -> dict:
    pid, ptype = p.get("id"), p.get("type")
    d = {"id": pid, "type": ptype}
    loc = p.get("location")
    if ptype.endswith("Vector") or ptype.endswith("Matrix") or ptype == "ByteArray":
        prefix = "Byte" if ptype == "ByteArray" else ptype[:-6]
        dt = np.dtype(_ELEM[prefix])
        if ptype.endswith("Matrix"):
            rows, cols = int(p.get("rows")), int(p.get("columns"))
            d["rows"], d["columns"] = rows, cols
            shape = (rows, cols)
        else:
            n = int(p.get("length"))
            d["length"] = n
            shape = (n,)
        if loc:
            d["location"] = _location_value(loc)
            buf = _block_bytes(fh, p, loc, p.text)
            if p.get("byteOrder") == "big":
                dt = dt.newbyteorder(">")
            val = np.frombuffer(buf, dtype=dt, count=int(np.prod(shape)))
            d["value"] = val.reshape(shape)
        else:
            d["value"] = np.zeros(shape, dt)
        d["dtype"] = d["value"].dtype
    elif ptype == "String":
        if p.get("value") is not None:
            d["value"] = p.get("value")
        elif loc:
            d["location"] = _location_value(loc)
            d["value"] = _block_bytes(fh, p, loc, p.text).decode("utf-8")
        else:
            d["value"] = p.text or ""
    elif ptype == "TimePoint":
        d["value"] = p.get("value") if p.get("value") is not None else (p.text or "")
    else:
        v = p.get("value")
        d["value"] = _parse_scalar(ptype, v if v is not None else (p.text or ""))
    for extra in ("comment", "format"):
        if p.get(extra) is not None:
            d[extra] = p.get(extra)
    return d


def _fits_value(v: str) -> str:
    """FITS 字符串值去掉两侧单引号(与原 xisf 包一致:'M 31' → M 31)。
    内部转义的 '' 不还原(原包也不还原;写回时原样写出,来回一致)。"""
    s = v.strip()
    if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
        s = s[1:-1].rstrip()
    return s


class XISF:
    """一个 XISF 文件(只读视图)。构造时解析文件头和全部元数据;像素在 read_image 时才读。"""

    def __init__(self, fname):
        self._fname = os.fspath(fname)
        with open(self._fname, "rb") as fh:
            pre = fh.read(16)
            if len(pre) < 16 or pre[:8] != _SIGNATURE:
                raise ValueError("不是 XISF 1.0 文件(签名不对):%s" % self._fname)
            hlen = struct.unpack("<I", pre[8:12])[0]
            header = fh.read(hlen)
            root = ET.fromstring(header.rstrip(b"\0"))
            self._images = []           # (元素, 元数据 dict)
            for im in root.findall(_Q + "Image"):
                self._images.append((im, self._image_meta(fh, im)))
            self._file_meta = {}
            md = root.find(_Q + "Metadata")
            if md is not None:
                for p in md.findall(_Q + "Property"):
                    pr = _parse_property(fh, p)
                    self._file_meta[pr["id"]] = {"id": pr["id"], "type": pr["type"], "value": pr["value"]}

    # ---- 元数据 ----
    @staticmethod
    def _image_meta(fh, im: ET.Element) -> dict:
        m = {}
        for k, v in im.attrib.items():
            if k == "geometry":
                m[k] = tuple(int(x) for x in v.split(":"))
            elif k == "location":
                m[k] = _location_value(v)
            else:
                m[k] = v
        fmt = m.get("sampleFormat")
        if fmt in _SAMPLE_FORMATS:
            m["dtype"] = _SAMPLE_FORMATS[fmt]
        fk = {}
        props = {}
        for ch in im:
            tag = ch.tag.replace(_Q, "")
            if tag == "FITSKeyword":
                fk.setdefault(ch.get("name"), []).append(
                    {"value": _fits_value(ch.get("value", "")), "comment": ch.get("comment", "")})
            elif tag == "Property":
                pr = _parse_property(fh, ch)
                props[pr["id"]] = pr
        m["FITSKeywords"] = fk
        m["XISFProperties"] = props
        return m

    def get_images_metadata(self) -> list:
        return [m for _, m in self._images]

    def get_file_metadata(self) -> dict:
        return self._file_meta

    # ---- 像素 ----
    def read_image(self, n: int = 0, data_format: str = "channels_last") -> np.ndarray:
        im, m = self._images[n]
        w, h, c = m["geometry"][0], m["geometry"][1], (m["geometry"][2] if len(m["geometry"]) > 2 else 1)
        dt = _SAMPLE_FORMATS[m["sampleFormat"]]
        if im.get("byteOrder") == "big":
            dt = dt.newbyteorder(">")
        loc = im.get("location")
        count = w * h * c
        with open(self._fname, "rb") as fh:
            if loc.startswith("attachment:") and not im.get("compression"):
                _, pos, _size = loc.split(":")
                fh.seek(int(pos))
                a = np.fromfile(fh, dtype=dt, count=count)
                if a.size != count:
                    raise ValueError("XISF:图像数据不完整:%s" % self._fname)
            else:
                a = np.frombuffer(_block_bytes(fh, im, loc, im.text), dtype=dt, count=count)
        if dt.byteorder == ">":
            a = a.astype(dt.newbyteorder("<"))
        if (im.get("pixelStorage") or "Planar") == "Normal":
            a = a.reshape(h, w, c)                       # 像素交错存放:本来就是通道在最后
            return a if data_format == "channels_last" else a.transpose(2, 0, 1)
        a = a.reshape(c, h, w)                           # Planar:逐通道整幅存放
        return a.transpose(1, 2, 0) if data_format == "channels_last" else a

    # ---- 写 ----
    @staticmethod
    def write(fname, im_data, creator_app=None, image_metadata=None, xisf_metadata=None,
              codec=None, shuffle=False, level=None):
        """写单幅图像的 XISF(不压缩;codec/shuffle/level 为兼容原接口保留,忽略)。
        im_data:(高, 宽, 通道) 或 (高, 宽);image_metadata 里的 id / FITSKeywords / XISFProperties 原样写回,
        几何、采样格式、色彩空间按数组本身重算(与原包相同)。xisf_metadata 里 XISF: 开头以外的属性也写回。"""
        # 【元数据必须按关键字传】2026-10-14 前有 14 处写成 write(out, 数组, *_read_meta(xn)),图像元数据落进
        #   creator_app、文件元数据落进 image_metadata;原包不报错,结果 FITS 关键字和天文解析解全丢。
        #   调用点已改用 recombine._meta_kw;再有人这么写,这里直接报错,别再静默丢图头。
        if creator_app is not None and not isinstance(creator_app, str):
            raise TypeError("XISF.write: creator_app must be a string; pass metadata as "
                            "image_metadata= / xisf_metadata= keywords")
        a = np.asarray(im_data)
        if a.ndim == 2:
            a = a[..., None]
        if a.ndim != 3:
            raise ValueError("XISF.write:图像数组必须是 (高, 宽, 通道),拿到 %s" % (a.shape,))
        fmt = _DTYPE_TO_FORMAT.get(np.dtype(a.dtype).newbyteorder("=") if a.dtype.itemsize > 1 else a.dtype)
        if fmt is None:
            raise ValueError("XISF.write:不支持的数据类型 %s" % a.dtype)
        h, w, c = a.shape
        planar = np.ascontiguousarray(a.transpose(2, 0, 1), dtype=a.dtype.newbyteorder("<"))
        img_bytes = memoryview(planar).cast("B")   # 直接写出,不再整幅拷贝一次
        meta = image_metadata or {}
        # 尺寸和元数据对不上(裁过/缩过)时,天文解析解与 WCS 关键字不再成立 → 不写出,只留其余图头
        g = meta.get("geometry")
        if g and tuple(g[:2]) != (w, h):
            meta = dict(meta)
            meta["XISFProperties"] = {k: v for k, v in (meta.get("XISFProperties") or {}).items()
                                      if "AstrometricSolution" not in str(k)}
            meta["FITSKeywords"] = {k: v for k, v in (meta.get("FITSKeywords") or {}).items()
                                    if not _WCS_KEY.match(str(k))}

        # 属性:先编码,大块放到附件区
        prop_xml, attachments = [], []           # attachments: [(占位编号, bytes)]
        for pid, pr in (meta.get("XISFProperties") or {}).items():
            enc = _encode_property(pid, pr)
            if enc is None:
                continue
            attrs, text, blob = enc
            if blob is not None:
                if len(blob) <= _MAX_INLINE:
                    attrs["location"] = "inline:base64"
                    text = base64.b64encode(blob).decode("ascii")
                else:
                    attrs["location"] = "@ATT%d@" % len(attachments)
                    attachments.append(blob)
            prop_xml.append((attrs, text))

        file_props = [
            ("XISF:CreationTime", "String", None,
             datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
            ("XISF:CreatorApplication", "String", None,
             creator_app or ("Python %d.%d.%d" % sys.version_info[:3])),
            ("XISF:CreatorModule", "String", None, "TTAstroPiLot xisfio 1.0"),
            ("XISF:CreatorOS", "String", None, {"win32": "Windows", "darwin": "macOS"}.get(sys.platform, platform.system())),
            ("XISF:BlockAlignmentSize", "UInt16", str(_ALIGN), None),
            ("XISF:MaxInlineBlockSize", "UInt16", str(_MAX_INLINE), None),
        ]
        extra_file = []
        for pid, pr in (xisf_metadata or {}).items():
            if str(pid).startswith("XISF:"):
                continue
            enc = _encode_property(pid, pr)
            if enc is not None and enc[2] is None:          # 文件级只写标量/字符串
                extra_file.append(enc[:2])

        def build(img_pos: int, att_pos: list) -> bytes:
            root = ET.Element("xisf", {"version": "1.0", "xmlns": _NS,
                                       "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
                                       "xsi:schemaLocation": _NS + " http://pixinsight.com/xisf/xisf-1.0.xsd"})
            ia = {"id": str(meta.get("id") or "image"), "geometry": "%d:%d:%d" % (w, h, c),
                  "sampleFormat": fmt}
            if fmt.startswith("Float"):
                ia["bounds"] = "0:1"
            ia["colorSpace"] = "Gray" if c < 3 else "RGB"
            ia["location"] = "attachment:%d:%d" % (img_pos, len(img_bytes))
            ie = ET.SubElement(root, "Image", ia)
            for name, entries in (meta.get("FITSKeywords") or {}).items():
                for e in entries:
                    ET.SubElement(ie, "FITSKeyword", {"name": str(name), "value": str(e.get("value", "")),
                                                      "comment": str(e.get("comment", ""))})
            for attrs, text in prop_xml:
                a2 = dict(attrs)
                lo = a2.get("location", "")
                if lo.startswith("@ATT"):
                    k = int(lo[4:-1])
                    a2["location"] = "attachment:%d:%d" % (att_pos[k], len(attachments[k]))
                pe = ET.SubElement(ie, "Property", a2)
                if text is not None:
                    pe.text = text
            me = ET.SubElement(root, "Metadata")
            for pid, ptype, val, text in file_props:
                at = {"id": pid, "type": ptype}
                if val is not None:
                    at["value"] = val
                pe = ET.SubElement(me, "Property", at)
                if text is not None:
                    pe.text = text
            for attrs, text in extra_file:
                pe = ET.SubElement(me, "Property", attrs)
                if text is not None:
                    pe.text = text
            return b'<?xml version="1.0" encoding="UTF-8"?>' + _tostring(root)

        def layout(hlen: int):
            pos = _align(16 + hlen)
            img_pos = pos
            pos = _align(pos + len(img_bytes))
            att = []
            for blob in attachments:
                att.append(pos)
                pos = _align(pos + len(blob))
            return img_pos, att

        # 位置写在文件头里、文件头长度又决定位置 → 迭代到不动点(位数变化最多几次)
        img_pos, att_pos = layout(0)
        for _ in range(8):
            hdr = build(img_pos, att_pos)
            n_img, n_att = layout(len(hdr))
            if (n_img, n_att) == (img_pos, att_pos):
                break
            img_pos, att_pos = n_img, n_att
        else:
            raise RuntimeError("XISF.write:文件头布局不收敛")

        with open(os.fspath(fname), "wb") as f:
            f.write(_SIGNATURE + struct.pack("<I", len(hdr)) + b"\0\0\0\0")
            f.write(hdr)
            f.write(b"\0" * (img_pos - 16 - len(hdr)))
            f.write(img_bytes)
            end = img_pos + len(img_bytes)
            for p, blob in zip(att_pos, attachments):
                f.write(b"\0" * (p - end))
                f.write(blob)
                end = p + len(blob)


def _align(n: int) -> int:
    return (n + _ALIGN - 1) // _ALIGN * _ALIGN


def _tostring(root: ET.Element) -> bytes:
    # 标签都不带命名空间前缀,默认命名空间作为字面属性 xmlns 写出
    return ET.tostring(root, encoding="unicode", short_empty_elements=True).encode("utf-8")


def _num_text(v) -> str:
    if isinstance(v, (bool, np.bool_)):
        return "true" if v else "false"
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        return repr(float(v))
    if isinstance(v, (complex, np.complexfloating)):
        return "(%r,%r)" % (float(v.real), float(v.imag))
    return str(v)


def _encode_property(pid, pr) -> tuple | None:
    """属性 dict → (XML 属性, 文本内容, 需要放进数据块的字节或 None)。认不出的类型返回 None(跳过,不报错)。"""
    if not isinstance(pr, dict):
        return None
    ptype = str(pr.get("type") or "")
    val = pr.get("value")
    attrs = {"id": str(pr.get("id") or pid), "type": ptype}
    for extra in ("comment", "format"):
        if pr.get(extra) is not None:
            attrs[extra] = str(pr[extra])
    if ptype in ("String",):
        return attrs, "" if val is None else str(val), None
    if ptype == "TimePoint":
        attrs["value"] = str(val)
        return attrs, None, None
    if ptype == "Boolean" or ptype in _INT_TYPES or ptype in _FLOAT_TYPES or ptype in _COMPLEX_TYPES:
        attrs["value"] = _num_text(val)
        return attrs, None, None
    if ptype.endswith("Vector") or ptype.endswith("Matrix") or ptype == "ByteArray":
        prefix = "Byte" if ptype == "ByteArray" else ptype[:-6]
        if prefix not in _ELEM:
            return None
        arr = np.asarray(val, dtype=np.dtype(_ELEM[prefix]))
        if ptype.endswith("Matrix"):
            arr = arr.reshape(int(pr.get("rows", arr.shape[0])), int(pr.get("columns", arr.shape[-1])))
            attrs["rows"], attrs["columns"] = str(arr.shape[0]), str(arr.shape[1])
        else:
            arr = arr.reshape(-1)
            attrs["length"] = str(arr.size)
        return attrs, None, np.ascontiguousarray(arr).tobytes()
    return None
