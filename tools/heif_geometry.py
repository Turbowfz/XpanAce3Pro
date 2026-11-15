#!/usr/bin/env python3
"""Read-only HEIF item/property geometry inventory. Run with python -I.

This reports declarations and references, not decoded pixels or HDR correctness.
No source bytes, item payloads or original photos are modified.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

LIMIT = 128 << 20


def boxes(data, start=0, end=None):
    end = len(data) if end is None else end
    count = 0
    while start < end:
        if start + 8 > end:
            raise ValueError("truncated box header")
        size, kind = struct.unpack_from(">I4s", data, start)
        header = 8
        if size == 1:
            if start + 16 > end:
                raise ValueError("truncated large box")
            size = struct.unpack_from(">Q", data, start + 8)[0]
            header = 16
        elif size == 0:
            size = end - start
        if size < header or start + size > end:
            raise ValueError("box extends beyond parent")
        count += 1
        if count > 16384:
            raise ValueError("box count limit")
        yield {"type": kind.decode("latin1"), "offset": start, "size": size,
               "payload": start + header, "end": start + size}
        start += size


class Fields:
    def __init__(self, data):
        self.data = data
        self.at = 0

    def take(self, size):
        if size < 0 or self.at + size > len(self.data):
            raise ValueError("truncated item field")
        value = self.data[self.at:self.at + size]
        self.at += size
        return value

    def number(self, size):
        return int.from_bytes(self.take(size), "big")

    def string(self):
        end = self.data.find(b"\0", self.at)
        if end < 0:
            raise ValueError("unterminated item string")
        value = self.take(end - self.at + 1)[:-1]
        return value.decode("utf-8", errors="replace")


def property_info(data, box):
    p = data[box["payload"]:box["end"]]
    result = {k: box[k] for k in ("type", "offset", "size")}
    kind = box["type"]
    if kind == "ispe" and len(p) == 12:
        result.update(width=int.from_bytes(p[4:8], "big"), height=int.from_bytes(p[8:12], "big"))
    elif kind == "clap" and len(p) == 32:
        result["rationals"] = list(struct.unpack(">8I", p))
    elif kind in ("irot", "imir"):
        result["raw"] = p.hex()
    elif kind == "pixi":
        result["raw"] = p.hex()
    elif kind == "auxC":
        result["aux_type"] = p[4:].split(b"\0", 1)[0].decode("utf-8", errors="replace")
    elif kind == "colr":
        result["colour_type"] = p[:4].decode("latin1")
        if p[:4] == b"nclx" and len(p) >= 11:
            result.update(primaries=int.from_bytes(p[4:6], "big"), transfer=int.from_bytes(p[6:8], "big"),
                          matrix=int.from_bytes(p[8:10], "big"), full_range=bool(p[10] & 128))
        else:
            result["payload_sha256"] = hashlib.sha256(p).hexdigest()
    elif kind == "hvcC":
        result["configuration_bytes"] = len(p)
    else:
        result["raw"] = p.hex() if len(p) <= 128 else p[:32].hex()
    return result


def inspect(path):
    if not path.is_file() or path.stat().st_size > LIMIT:
        raise ValueError("source must be a bounded regular photo")
    data = path.read_bytes()
    top = list(boxes(data))
    meta = [b for b in top if b["type"] == "meta"]
    if len(meta) != 1:
        raise ValueError("one top-level meta required")
    children = list(boxes(data, meta[0]["payload"] + 4, meta[0]["end"]))
    items, refs, props, associations = {}, [], [], {}
    primary = None
    locations = {}
    for b in children:
        p = data[b["payload"]:b["end"]]
        if b["type"] == "pitm":
            primary = int.from_bytes(p[4:6] if p[0] == 0 else p[4:8], "big")
        elif b["type"] == "iinf":
            for infe in boxes(data, b["payload"] + (6 if p[0] == 0 else 8), b["end"]):
                q = Fields(data[infe["payload"]:infe["end"]])
                version = q.number(1)
                q.take(3)
                if version not in (2, 3):
                    raise ValueError("unsupported infe version")
                item_id = q.number(2 if version == 2 else 4)
                protection = q.number(2)
                kind = q.take(4).decode("latin1")
                item = {"id": item_id, "type": kind, "name": q.string(), "protection": protection}
                if kind == "mime":
                    item["content_type"] = q.string()
                    if q.at < len(q.data):
                        item["content_encoding"] = q.string()
                if item_id in items:
                    raise ValueError("duplicate item ID")
                items[item_id] = item
        elif b["type"] == "iloc":
            q = Fields(p)
            version = q.number(1)
            q.take(3)
            first, second = q.number(1), q.number(1)
            offset_size, length_size = first >> 4, first & 15
            base_size = second >> 4
            index_size = second & 15 if version in (1, 2) else 0
            count = q.number(2 if version < 2 else 4)
            if count > 4096:
                raise ValueError("item count limit")
            for _ in range(count):
                item_id = q.number(2 if version < 2 else 4)
                method = q.number(2) & 15 if version in (1, 2) else 0
                reference = q.number(2)
                base = q.number(base_size)
                extent_count = q.number(2)
                if extent_count > 16384:
                    raise ValueError("extent count limit")
                extents = []
                for _ in range(extent_count):
                    index = q.number(index_size)
                    offset = q.number(offset_size)
                    length = q.number(length_size)
                    extents.append({"offset": base + offset, "length": length, "index": index})
                locations[item_id] = {"method": method, "data_reference": reference, "extents": extents}
        elif b["type"] == "iref":
            id_size = 2 if p[0] == 0 else 4
            for reference in boxes(data, b["payload"] + 4, b["end"]):
                q = Fields(data[reference["payload"]:reference["end"]])
                source = q.number(id_size)
                count = q.number(2)
                if count > 4096:
                    raise ValueError("reference count limit")
                refs.append({"type": reference["type"], "from": source,
                             "to": [q.number(id_size) for _ in range(count)]})
        elif b["type"] == "iprp":
            for child in boxes(data, b["payload"], b["end"]):
                if child["type"] == "ipco":
                    props = [property_info(data, z) for z in boxes(data, child["payload"], child["end"])]
                elif child["type"] == "ipma":
                    q = Fields(data[child["payload"]:child["end"]])
                    version, flags = q.number(1), q.number(3)
                    count = q.number(4)
                    if count > 4096:
                        raise ValueError("association count limit")
                    for _ in range(count):
                        item_id = q.number(2 if version < 1 else 4)
                        association_count = q.number(1)
                        entries = []
                        for _ in range(association_count):
                            width = 2 if flags & 1 else 1
                            value = q.number(width)
                            entries.append({"index": value & (0x7fff if width == 2 else 0x7f),
                                            "essential": bool(value & (0x8000 if width == 2 else 0x80))})
                        associations[item_id] = entries
    idat = [b for b in children if b["type"] == "idat"]
    for item_id, item in items.items():
        location = locations.get(item_id)
        item["location"] = location
        item["properties"] = [{**props[a["index"] - 1], "essential": a["essential"]}
                              for a in associations.get(item_id, []) if 0 < a["index"] <= len(props)]
        payload = bytearray()
        if location and location["data_reference"] == 0 and location["method"] in (0, 1):
            for e in location["extents"]:
                at = e["offset"] + (idat[0]["payload"] if location["method"] == 1 and len(idat) == 1 else 0)
                if e["length"] > len(data) - at or at < 0:
                    raise ValueError("item extent beyond photo")
                payload.extend(data[at:at + e["length"]])
        item["payload_bytes"] = len(payload)
        if item["type"] == "grid" and len(payload) >= 8:
            wide = bool(payload[1] & 1)
            size = 4 if wide else 2
            item["grid"] = {"version": payload[0], "flags": payload[1], "rows": payload[2] + 1,
                            "columns": payload[3] + 1,
                            "width": int.from_bytes(payload[4:4 + size], "big"),
                            "height": int.from_bytes(payload[4 + size:4 + 2 * size], "big")}
        elif item["type"] == "tmap":
            item["raw"] = payload.hex()
            if len(payload) == 62:
                item["alternate_headroom_max"] = list(struct.unpack_from(">II", payload, 14))
                item["gain_max"] = list(struct.unpack_from(">iI", payload, 30))
        elif item["type"] == "mime":
            item["text"] = payload[:65536].decode("utf-8", errors="replace")
        elif item["type"] == "Exif":
            item["payload_sha256"] = hashlib.sha256(payload).hexdigest()
    return {"source": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "primary_item": primary, "top_boxes": [{k: b[k] for k in ("type", "offset", "size")} for b in top],
            "items": list(items.values()), "references": refs, "property_count": len(props)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("photo", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("report must be a new file")
    report = inspect(args.photo)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"sha256": report["sha256"], "bytes": report["bytes"],
                      "primary_item": report["primary_item"], "item_count": len(report["items"]),
                      "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
