"""Lossless XISF header edits: change FITS keywords / properties of one image while copying pixel data,
thumbnails, ICC profiles and every other attachment byte for byte.

Used to write a fresh astrometric solution back into the user's original file. Going through XISF.write
would re-encode the image and drop thumbnails/ICC profiles, which is not acceptable for an original.
"""
from __future__ import annotations

import os
import re
import struct
import tempfile
import xml.etree.ElementTree as ET

_NS = "http://www.pixinsight.com/xisf"
_Q = "{%s}" % _NS
_SIGNATURE = b"XISF0100"
_ALIGN = 4096
_LOC = re.compile(r"^attachment:(\d+):(\d+)$")


def _serialize(root: ET.Element) -> bytes:
    body = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    return b'<?xml version="1.0" encoding="UTF-8"?>' + body.encode("utf-8")


def update_header(path, set_keywords=None, remove_keywords=None, remove_property=None,
                  image_index: int = 0) -> dict:
    """Edit one image's header in place (through a temporary file + atomic replace).

    set_keywords:    [(name, raw_value, comment)] added after the existing FITS keywords. raw_value is the
                     FITS value as text, strings already quoted (e.g. "'RA---TAN-SIP'").
    remove_keywords: callable(name) -> bool, keywords dropped first (e.g. stale WCS keywords).
    remove_property: callable(id) -> bool, image properties dropped (e.g. a stale AstrometricSolution).
    If the new header no longer fits in front of the first data block, all attachments move by the same
    multiple of the block alignment, so their relative layout is unchanged.
    """
    path = os.fspath(path)
    with open(path, "rb") as fh:
        pre = fh.read(16)
        if pre[:8] != _SIGNATURE:
            raise ValueError("not an XISF 1.0 file: %s" % path)
        hlen = struct.unpack("<I", pre[8:12])[0]
        header = fh.read(hlen).rstrip(b"\0")
    ET.register_namespace("", _NS)
    ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
    root = ET.fromstring(header)
    im = root.findall(_Q + "Image")[image_index]
    info = {"keywords_removed": 0, "keywords_added": 0, "properties_removed": 0, "shifted": 0}
    for ch in list(im):
        tag = ch.tag.replace(_Q, "")
        if tag == "FITSKeyword" and remove_keywords and remove_keywords(ch.get("name", "")):
            im.remove(ch)
            info["keywords_removed"] += 1
        elif tag == "Property" and remove_property and remove_property(ch.get("id", "")):
            im.remove(ch)
            info["properties_removed"] += 1
    kids = list(im)
    last_kw = max([i for i, ch in enumerate(kids) if ch.tag == _Q + "FITSKeyword"], default=-1)
    for k, (name, value, comment) in enumerate(set_keywords or []):
        el = ET.Element(_Q + "FITSKeyword", {"name": str(name), "value": str(value), "comment": str(comment)})
        im.insert(last_kw + 1 + k, el)
        info["keywords_added"] += 1

    located = [(el, int(_LOC.match(el.get("location")).group(1)), _LOC.match(el.get("location")).group(2))
               for el in root.iter() if el.get("location") and _LOC.match(el.get("location"))]
    first = min((pos for _, pos, _ in located), default=None)
    shift = 0
    new_header = _serialize(root)
    while first is not None and 16 + len(new_header) > first + shift:
        shift += _ALIGN
        for el, pos, size in located:
            el.set("location", "attachment:%d:%s" % (pos + shift, size))
        new_header = _serialize(root)          # longer numbers can grow the header again -> loop
    info["shifted"] = shift
    data_start = first + shift if first is not None else 16 + len(new_header)

    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".skyannot_", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "wb") as out, open(path, "rb") as src:
            out.write(_SIGNATURE + struct.pack("<I", len(new_header)) + b"\0\0\0\0")
            out.write(new_header)
            out.write(b"\0" * (data_start - 16 - len(new_header)))
            if first is not None:
                src.seek(first)
                while True:
                    buf = src.read(16 << 20)
                    if not buf:
                        break
                    out.write(buf)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return info
