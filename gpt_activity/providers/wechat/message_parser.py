"""WeChat payload normalization, preserving EasyInternship v1 semantics."""

from __future__ import annotations

import re


RED_PACKET_TYPE = 0x7D100000031
TYPE_LABELS = {
    1: "文本", 3: "图片", 34: "语音", 43: "视频", 47: "动画表情",
    48: "位置", 49: "文件/链接/卡片", 50: "音视频通话",
    10000: "系统消息", 11000: "动画表情",
}


def get_base_type(local_type):
    if local_type == RED_PACKET_TYPE or local_type in TYPE_LABELS:
        return local_type
    if isinstance(local_type, int):
        low32 = local_type & 0xFFFFFFFF
        if low32 in TYPE_LABELS:
            return low32
        low8 = low32 & 0xFF
        if low8 in TYPE_LABELS:
            return low8
    return local_type


def type_label(local_type) -> str:
    if local_type == RED_PACKET_TYPE:
        return "红包"
    return TYPE_LABELS.get(get_base_type(local_type), f"类型{local_type}")


def decode_payload(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if not isinstance(value, bytes):
        return str(value)
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        pass
    magic = b"\x28\xb5\x2f\xfd"
    position = value.find(magic)
    if position >= 0:
        try:
            import zstandard as zstd
            return zstd.ZstdDecompressor().decompress(
                value[position:], max_output_size=5_000_000
            ).decode("utf-8", errors="ignore")
        except Exception:
            pass
    return ""


def extract_xml_text(text: str, tag: str) -> str:
    if not text:
        return ""
    for pattern in (rf"<{tag}><!\[CDATA\[(.*?)\]\]></{tag}>", rf"<{tag}>(.*?)</{tag}>"):
        match = re.search(pattern, text, re.I | re.S)
        if match:
            return " ".join(re.sub(r"<[^>]+>", "", match.group(1)).split()).strip()
    return ""


def _summary(text: str, base_type, local_type) -> str:
    if base_type == 1:
        return text or "[文本]"
    labels = {3: "[图片]", 34: "[语音]", 43: "[视频]", 47: "[动画表情]", 11000: "[动画表情]", 48: "[位置]", 50: "[音视频通话]"}
    if base_type in labels:
        return labels[base_type]
    if base_type == 10000:
        content = extract_xml_text(text, "content")
        return f"[系统消息] {content}" if content else "[系统消息]"
    if local_type == RED_PACKET_TYPE:
        detail = extract_xml_text(text, "des") or extract_xml_text(text, "sendertitle")
        return f"[红包] {detail}" if detail else "[红包]"
    if base_type == 49:
        title, description = extract_xml_text(text, "title"), extract_xml_text(text, "des")
        detail = " - ".join(part for part in (title, description) if part)
        return f"[卡片] {detail}" if detail else "[文件/链接/卡片]"
    if text and len(text) < 500 and not text.lstrip().startswith("<"):
        return text
    return f"[{type_label(local_type)}]"


def clean_content(row: dict, known_ids: set[str], conversation_wxid: str) -> str:
    from .sender_resolver import sender_prefix
    local_type = row.get("local_type")
    raw = decode_payload(row.get("message_content"))
    compressed = decode_payload(row.get("compress_content"))
    text = raw or compressed or ""
    _, cut = sender_prefix(text, known_ids, conversation_wxid)
    if cut:
        text = text.lstrip("\ufeff")[cut:].strip()
    return _summary(text, get_base_type(local_type), local_type)


clean_group_content = clean_content
