#!/usr/bin/env python3
"""Offline XPAN evidence/patch units, NOT a module or a C17 configuration generator.

Commands (run with python -I):
  config_patch.py analyze --manifest ORIGINALS.json [--comparison-dir OVERLAY]
  config_patch.py patch-json --manifest ORIGINALS.json --out-dir NEW_DIRECTORY
  config_patch.py patch-pb --manifest ORIGINALS.json --out-dir NEW_DIRECTORY

Manifest v1 is a caller attestation, not a signature or proof of stock origin:
  {"manifest_version":1,"platform":"C16","source_kind":"stock-original",
   "original_validated":true,"validation":{"method":"...","reference":"..."},
   "files":{"camera_unit_config":{"path":"stock/camera_unit_config",
             "size":18732,"sha256":"...","format":"json"}},
   "schema":{"path":"sdk/ProtobufFeatureConfig.smali","format":"smali",
             "size":123,"sha256":"..."}}
Paths are relative to the manifest, cannot escape it, and cannot be symlinks.
Schema is required only for indexed PB. C16 CLI inputs additionally match the
locally investigated stock hashes. No manifest is generated/approved by this tool.

JSON units change existing support and rear_main lists only; --c16-crop is an
explicit observed 4096x1512 recipe, not proof of HAL support. Indexed PB copies
ONLY the target's native photo_mode, including its unknown fields and indices.
That full native clone reproduces the observed C16 overlay; a smaller runtime
feature subset is NOT established. No donor feature records or pool insertions.
Encrypted envelopes can be decrypted for evidence ONLY: header/trailer semantics
are unverified, so encryption/publication of an ODM binary is deliberately absent.
"""
from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

MAX_PAYLOAD = 8 * 1024 * 1024
MAX_DEPTH = 64
MAX_FIELDS = 100000
SCHEMA_SHA256 = "a3d0cf64b076d0a39c05215c9c6951609f2996c9d05b244d3353c3e78d0c1ceb"
C16_LIBRARY_SHA256 = "24fb1c5834368757ae372699dc701ff00d0c75a7b18b2f5e802b0b2143bd4ddf"
C16_STOCK_SHA256 = {
    "camera_unit_config": "2adeaa2e3af2657e988ac35d62fc53f5fe795276e204e857d6e9d7738634ad21",
    "camera_unit_feature_config.protobuf": "e3b0144756531f57227e6e197dddddbc6f13dc1553f727aca383176f23255a58",
    "oplus_camera_config": "c26086ad5d1f90bc89aba87d305aa5cc1c186b9276daeb914bdf6780c33f50b6",
    "libOplusSecurity.so": C16_LIBRARY_SHA256,
}
C16_OVERLAY_SHA256 = {
    "6dff1eaf085af1e61817eda9c34e4937e67e2ef4416cbee31409051e43ce39f7",
    "baab74e1dae8501a0ecf2c9d75847a64cfbb2afee91945e2311edb9e1caedd2e",
    "2bca1b3fd2251c1742a668c3bbca41667332ab2b954b2774163f40ff0ebeca3a",
}
SUPPORT = "com.oplus.feature.xpan.mode.support"
PICTURE = "com.oplus.xpan.main.picturesize"
C16_MAIN_CROPS = (
    {"VendorTag": "com.oplus.xpan.crop.region.main.portrait", "Type": "Int32", "Count": "4", "Value": "780,0,1512,4096"},
    {"VendorTag": "com.oplus.xpan.crop.region.main.landscape", "Type": "Int32", "Count": "4", "Value": "0,780,4096,1512"},
)


class ConfigError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def fail(code, message):
    raise ConfigError(code, message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def bounded(data):
    if not isinstance(data, bytes) or len(data) > MAX_PAYLOAD:
        fail("E_SIZE", "payload must be bytes and at most 8 MiB")
    return data


def read_bounded(path):
    with Path(path).open("rb") as stream:
        return bounded(stream.read(MAX_PAYLOAD + 1))


class JsonDocument:
    """Strict JSON with token spans; retain all unrelated bytes, fields and lists."""
    def __init__(self, data, allow_c16_padding=False):
        bounded(data)
        try:
            self.text = data.decode("utf-8")
        except UnicodeError:
            fail("E_JSON", "JSON must be UTF-8")
        self.spans = {}
        self.count = 0
        self.decoder = json.JSONDecoder(parse_float=Decimal,
                                        parse_constant=lambda _: fail("E_JSON", "nonfinite JSON number"))
        try:
            self.obj, end = self._value(self._ws(0), (), 0)
        except (ValueError, RecursionError) as exc:
            if isinstance(exc, ConfigError):
                raise
            fail("E_JSON", "invalid JSON: " + str(exc))
        suffix = self.text[end:]
        # Stock C16 plaintext has seven 0x07 bytes, then overlay zero padding.
        # Accept only that observed suffix, and only in read-only decryption.
        if suffix.strip(" \t\r\n"):
            if not allow_c16_padding or not re.fullmatch(r"[ \t\r\n]*\x07{7}\x00*", suffix):
                fail("E_JSON", "trailing data after JSON")
        self.data = data

    def _ws(self, pos):
        while pos < len(self.text) and self.text[pos] in " \t\r\n":
            pos += 1
        return pos

    def _value(self, pos, path, depth):
        if depth > MAX_DEPTH:
            fail("E_DEPTH", "JSON nesting exceeds limit")
        self.count += 1
        if self.count > MAX_FIELDS:
            fail("E_FIELDS", "too many JSON values")
        start = pos
        if pos >= len(self.text):
            fail("E_JSON", "truncated JSON")
        char = self.text[pos]
        if char in "{[":
            is_object = char == "{"
            result = {} if is_object else []
            closing = "}" if is_object else "]"
            pos = self._ws(pos + 1)
            if pos < len(self.text) and self.text[pos] == closing:
                pos += 1
            else:
                while True:
                    if is_object:
                        if pos >= len(self.text) or self.text[pos] != '"':
                            fail("E_JSON", "object key must be a string")
                        key, pos = self.decoder.raw_decode(self.text, pos)
                        if key in result:
                            fail("E_JSON", "duplicate JSON key: " + key)
                        pos = self._ws(pos)
                        if pos >= len(self.text) or self.text[pos] != ":":
                            fail("E_JSON", "missing colon")
                        pos = self._ws(pos + 1)
                    else:
                        key = len(result)
                    value, pos = self._value(pos, path + (key,), depth + 1)
                    if is_object:
                        result[key] = value
                    else:
                        result.append(value)
                    pos = self._ws(pos)
                    if pos >= len(self.text):
                        fail("E_JSON", "truncated container")
                    if self.text[pos] == closing:
                        pos += 1
                        break
                    if self.text[pos] != ",":
                        fail("E_JSON", "missing comma")
                    pos = self._ws(pos + 1)
        else:
            result, pos = self.decoder.raw_decode(self.text, pos)
        self.spans[path] = (start, pos)
        return result, pos

    def emit(self, replacements, expected):
        edits = []
        for path, value in replacements.items():
            start, end = self.spans[path]
            edits.append((start, end, json.dumps(value, ensure_ascii=False, allow_nan=False)))
        text = self.text
        for start, end, replacement in sorted(edits, reverse=True):
            text = text[:start] + replacement + text[end:]
        output = bounded(text.encode("utf-8"))
        if JsonDocument(output).obj != expected:
            fail("E_PRESERVATION", "JSON semantic preservation check failed")
        return output


def semantic_diff(old, new, path=()):
    """Order-sensitive arrays; absent and null are different. No tag projection."""
    if type(old) is not type(new):
        return [{"path": list(path), "old": old, "new": new, "kind": "replace"}]
    changes = []
    if isinstance(old, dict):
        for key in sorted(old.keys() | new.keys()):
            if key not in old:
                changes.append({"path": list(path + (key,)), "new": new[key], "kind": "add"})
            elif key not in new:
                changes.append({"path": list(path + (key,)), "old": old[key], "kind": "remove"})
            else:
                changes.extend(semantic_diff(old[key], new[key], path + (key,)))
    elif isinstance(old, list):
        for i in range(max(len(old), len(new))):
            if i >= len(old):
                changes.append({"path": list(path + (i,)), "new": new[i], "kind": "add"})
            elif i >= len(new):
                changes.append({"path": list(path + (i,)), "old": old[i], "kind": "remove"})
            else:
                changes.extend(semantic_diff(old[i], new[i], path + (i,)))
    elif old != new:
        changes.append({"path": list(path), "old": old, "new": new, "kind": "replace"})
    return changes


def patch_unit_json(data, c16_crop=False):
    doc = JsonDocument(data)
    obj = doc.obj
    if not isinstance(obj, dict):
        fail("E_JSON_SCHEMA", "camera_unit_config must be an object")
    operations = obj.get("mode_operation_mode")
    if not isinstance(operations, dict) or not isinstance(operations.get("xpan_mode"), str) or not operations["xpan_mode"]:
        fail("E_NATIVE_MODE", "native mode_operation_mode.xpan_mode is required; no guessed operation mode")
    expected = copy.deepcopy(obj)
    edits = {}
    for section in ("mode_type_list", "universal_mode_type_list"):
        if section not in obj and section == "universal_mode_type_list":
            continue
        lists = obj.get(section)
        modes = lists.get("rear_main") if isinstance(lists, dict) else None
        if not isinstance(modes, list) or not all(isinstance(x, str) for x in modes):
            fail("E_JSON_SCHEMA", section + ".rear_main must be an existing string list")
        if modes.count("xpan_mode") > 1:
            fail("E_AMBIGUOUS", "duplicate xpan_mode in rear_main")
        if "xpan_mode" not in modes:
            edits[(section, "rear_main")] = modes + ["xpan_mode"]
            expected[section]["rear_main"] = edits[(section, "rear_main")]
    if c16_crop:
        tags = obj.get("vendor_tag_type_values")
        if not isinstance(tags, dict) or tags.get(PICTURE) not in ("8192x3724", "4096x1512"):
            fail("E_CROP", "C16 native main picturesize does not match observed recipe")
        if tags[PICTURE] != "4096x1512":
            edits[("vendor_tag_type_values", PICTURE)] = "4096x1512"
            expected["vendor_tag_type_values"][PICTURE] = "4096x1512"
    return doc.emit(edits, expected) if edits else data


def vendor_records(obj):
    records = obj.get("file_data") if isinstance(obj, dict) else None
    if not isinstance(records, list):
        fail("E_JSON_SCHEMA", "file_data must be an existing list")
    tags = {}
    for i, record in enumerate(records):
        if not isinstance(record, dict) or not isinstance(record.get("VendorTag"), str):
            fail("E_JSON_SCHEMA", "invalid vendor-tag record")
        name = record["VendorTag"]
        tags.setdefault(name, []).append((i, record))
    return records, tags


def unique_vendor(tags, name):
    matches = tags.get(name, [])
    if len(matches) != 1:
        fail("E_AMBIGUOUS", "target vendor tag must be unique: " + name)
    return matches[0]


def patch_vendor_json(data, c16_crop=False):
    doc = JsonDocument(data)
    records, tags = vendor_records(doc.obj)
    expected = copy.deepcopy(doc.obj)
    edits = {}
    if SUPPORT not in tags:
        fail("E_NATIVE_FLAG", "native XPAN support record is required; no donor records")
    i, flag = unique_vendor(tags, SUPPORT)
    if flag.get("Type") != "Byte" or flag.get("Count") != "1" or flag.get("Value") not in ("0", "1"):
        fail("E_JSON_SCHEMA", "unverified XPAN support record")
    if flag["Value"] == "0":
        edits[("file_data", i, "Value")] = "1"
        expected["file_data"][i]["Value"] = "1"
    additions = []
    if c16_crop:
        if PICTURE not in tags:
            fail("E_CROP", "native main picturesize record required")
        i, record = unique_vendor(tags, PICTURE)
        if record.get("Type") != "Int32" or record.get("Count") != "2" or record.get("Value") not in ("8192x3724", "4096x1512"):
            fail("E_CROP", "main picturesize metadata does not match C16 evidence")
        if record["Value"] != "4096x1512":
            edits[("file_data", i, "Value")] = "4096x1512"
            expected["file_data"][i]["Value"] = "4096x1512"
        for crop in C16_MAIN_CROPS:
            if crop["VendorTag"] in tags:
                _, existing = unique_vendor(tags, crop["VendorTag"])
                if any(existing.get(k) != v for k, v in crop.items()):
                    fail("E_CROP", "conflicting native crop; geometry will not be guessed")
            else:
                additions.append(crop)
    output = doc.emit(edits, {**expected}) if edits else data
    if additions:
        # Insert only new, observed main-crop records; original records stay raw.
        current = JsonDocument(output)
        _, end = current.spans[("file_data",)]
        insert = ("," if records else "") + "\n" + ",\n".join(json.dumps(x) for x in additions)
        output = bounded((current.text[:end - 1] + insert + current.text[end - 1:]).encode("utf-8"))
        expected["file_data"].extend(copy.deepcopy(additions))
        if JsonDocument(output).obj != expected:
            fail("E_PRESERVATION", "vendor JSON preservation check failed")
    return output


def read_varint(data, pos):
    value = 0
    for i in range(10):
        if pos >= len(data):
            fail("E_PB_WIRE", "truncated varint")
        byte = data[pos]
        pos += 1
        if i == 9 and byte > 1:
            fail("E_PB_WIRE", "varint overflows uint64")
        value |= (byte & 127) << (i * 7)
        if byte < 128:
            return value, pos
    fail("E_PB_WIRE", "varint exceeds ten bytes")


def encode_varint(value):
    if not isinstance(value, int) or not 0 <= value < 2**64:
        fail("E_PB_WIRE", "invalid uint64")
    out = bytearray()
    while value > 127:
        out.append((value & 127) | 128)
        value >>= 7
    out.append(value)
    return bytes(out)


@dataclass(frozen=True)
class WireField:
    number: int
    wire_type: int
    raw: bytes
    key: bytes
    value: object

    def with_payload(self, payload):
        if self.wire_type != 2:
            fail("E_PB_WIRE", "only length-delimited payload can be replaced")
        bounded(payload)
        return self.key + encode_varint(len(payload)) + payload


def parse_wire(data):
    """Never guesses whether a bytes/string field is a nested message."""
    bounded(data)
    count = 0

    def fields(pos, depth, end_group=None):
        nonlocal count
        if depth > MAX_DEPTH:
            fail("E_DEPTH", "protobuf groups exceed nesting limit")
        result = []
        while pos < len(data):
            count += 1
            if count > MAX_FIELDS:
                fail("E_FIELDS", "too many protobuf fields")
            start = pos
            tag, pos = read_varint(data, pos)
            number, wt = tag >> 3, tag & 7
            if not 1 <= number < 2**29 or wt > 5:
                fail("E_PB_WIRE", "invalid protobuf tag")
            key = data[start:pos]
            if wt == 4:
                if end_group != number:
                    fail("E_PB_WIRE", "unmatched end group")
                return result, pos
            if wt == 0:
                value, pos = read_varint(data, pos)
            elif wt == 2:
                length, pos = read_varint(data, pos)
                if length > len(data) - pos:
                    fail("E_PB_WIRE", "length exceeds payload")
                value = data[pos:pos + length]
                pos += length
            elif wt == 3:
                value_start = pos
                _, pos = fields(pos, depth + 1, number)
                value = data[value_start:pos]
            else:
                length = 8 if wt == 1 else 4
                if pos + length > len(data):
                    fail("E_PB_WIRE", "truncated fixed-width field")
                value = data[pos:pos + length]
                pos += length
            result.append(WireField(number, wt, data[start:pos], key, value))
        if end_group is not None:
            fail("E_PB_WIRE", "unterminated group")
        return result, pos

    return fields(0, 0)[0]


def serialize_wire(fields):
    return bounded(b"".join(x.raw for x in fields))


def length_field(number, payload):
    return encode_varint((number << 3) | 2) + encode_varint(len(payload)) + payload


def verify_schema(data, form="descriptor"):
    bounded(data)
    if form == "smali":
        try:
            candidates = re.findall(r'^\s*const-string(?:/jumbo)?\s+\w+,\s*("(?:[^"\\]|\\.)*")\s*$', data.decode("utf-8"), re.MULTILINE)
            descriptors = [json.loads(x).encode("latin-1") for x in candidates if "camera_unit_feature_config.proto" in x]
        except (ValueError, UnicodeError):
            fail("E_SCHEMA", "cannot decode embedded descriptor")
        if len(descriptors) != 1:
            fail("E_SCHEMA", "exactly one embedded descriptor required")
        data = descriptors[0]
    elif form != "descriptor":
        fail("E_SCHEMA", "expected raw descriptor or smali")
    if sha256(data) != SCHEMA_SHA256:
        fail("E_SCHEMA", "unknown semantic descriptor; indexed PB mutation is unsupported")
    return SCHEMA_SHA256


# Verified from the full FileDescriptorProto above (proto3, zero-based str_pool).
# Value tuple: semantic name, scalar/message kind, repeated, string-pool index.
S = lambda name, kind, repeated=False, index=False: (name, kind, repeated, index)
PB_SCHEMA = {
    "FeatureTable": {1: S("str_pool", "string", True), 2: S("camera_feature_table", "CameraFeatureTable"), 3: S("camera_group_feature_table", "CameraGroupFeatureTable"), 4: S("version", "string")},
    "CameraFeatureTable": {1: S("modes", "ModeEntry", True)},
    "ModeEntry": {1: S("key", "string"), 2: S("value", "ModeFeatureTable")},
    "ModeFeatureTable": {1: S("cameras", "CameraEntry", True)},
    "CameraEntry": {1: S("key", "int", index=True), 2: S("value", "CameraTypeFeatureTable")},
    "CameraTypeFeatureTable": {1: S("features", "Feature", True)},
    "Feature": {**{i: S(name, "int", index=True) for i, name in enumerate(("feature_name_index", "feature_key_name_index", "feature_value_range_index", "feature_default_Value_index", "entry_type_index", "feature_value_type_index"), 1)}, 7: S("group_conflict", "bool"), 8: S("conflicts", "ConflictEntry", True)},
    "ConflictEntry": {1: S("key", "int", index=True), 2: S("value", "ConflictFeatureList")},
    "ConflictFeatureList": {1: S("features", "ConflictFeature", True)},
    "ConflictFeature": {1: S("name", "int", index=True), 2: S("value", "int", index=True), 3: S("conflict_value", "int", index=True)},
    "CameraGroupFeatureTable": {1: S("modes", "GroupModeEntry", True)},
    "GroupModeEntry": {1: S("key", "string"), 2: S("value", "ModeGroupFeatureTable")},
    "ModeGroupFeatureTable": {1: S("cameras", "GroupCameraEntry", True)},
    "GroupCameraEntry": {1: S("key", "int", index=True), 2: S("value", "CameraTypeFeatureTableList")},
    "CameraTypeFeatureTableList": {1: S("tables", "CameraTypeFeatureTable", True)},
}


def decode_message(data, kind, pool, depth=0):
    if depth > MAX_DEPTH:
        fail("E_DEPTH", "semantic PB depth exceeded")
    schema = PB_SCHEMA[kind]
    result = {"unknown_wire": []}
    for _, (name, scalar, repeated, _) in schema.items():
        result[name] = [] if repeated else ("" if scalar == "string" else False if scalar == "bool" else 0 if scalar == "int" else None)
    seen = set()
    for field in parse_wire(data):
        if field.number not in schema:
            result["unknown_wire"].append(field.raw.hex())
            continue
        name, scalar, repeated, index = schema[field.number]
        if not repeated and name in seen:
            fail("E_AMBIGUOUS", "duplicate singular PB field: " + kind + "." + name)
        seen.add(name)
        expected_wt = 0 if scalar in ("int", "bool") else 2
        if field.wire_type != expected_wt:
            fail("E_SCHEMA", "known PB field has incompatible wire type")
        if scalar == "string":
            try:
                value = field.value.decode("utf-8")
            except UnicodeError:
                fail("E_SCHEMA", "invalid UTF-8 in PB string")
        elif scalar in ("int", "bool"):
            value = field.value
            if value > 2**31 - 1 or (scalar == "bool" and value not in (0, 1)):
                fail("E_SCHEMA", "unsupported negative/overflow int32 or noncanonical bool")
        else:
            value = decode_message(field.value, scalar, pool, depth + 1)
        if index and not 0 <= value < len(pool):
            fail("E_INDEX", "string-pool index outside table")
        if repeated:
            result[name].append(value)
        else:
            result[name] = value
    # Proto3 omitted index fields default to zero; validate those too.
    for _, (name, _, _, index) in schema.items():
        if index and not 0 <= result[name] < len(pool):
            fail("E_INDEX", "default string-pool index outside table")
    if kind.endswith("Entry"):
        if result["value"] is None:
            fail("E_SCHEMA", "map entry lacks native value")
    for name in ("modes", "cameras", "conflicts"):
        if name in result:
            keys = [x["key"] for x in result[name]]
            if len(keys) != len(set(keys)):
                fail("E_AMBIGUOUS", "duplicate protobuf map key")
    return result


def inspect_indexed_pb(data, descriptor, form="descriptor"):
    verify_schema(descriptor, form)
    fields = parse_wire(data)
    pool = []
    for field in fields:
        if field.number == 1:
            if field.wire_type != 2:
                fail("E_SCHEMA", "string-pool wire type mismatch")
            try:
                pool.append(field.value.decode("utf-8"))
            except UnicodeError:
                fail("E_SCHEMA", "invalid string pool UTF-8")
    semantics = decode_message(data, "FeatureTable", pool)
    if semantics["camera_feature_table"] is None:
        fail("E_SCHEMA", "native camera feature table required")
    return semantics


def patch_indexed_pb(data, descriptor, form="descriptor"):
    """Observed C16 recipe: append native photo clone; no feature/table synthesis."""
    before = inspect_indexed_pb(data, descriptor, form)
    modes = before["camera_feature_table"]["modes"]
    photo = next((x for x in modes if x["key"] == "photo_mode"), None)
    if photo is None:
        fail("E_NATIVE_PHOTO", "target-native photo_mode is required")
    pool = before["str_pool"]
    if not any(pool[x["key"]] == "rear_main" for x in photo["value"]["cameras"]):
        fail("E_NATIVE_PHOTO", "native photo_mode has no rear_main camera table")
    existing = next((x for x in modes if x["key"] == "xpan_mode"), None)
    expected_entry = copy.deepcopy(photo)
    expected_entry["key"] = "xpan_mode"
    if existing is not None:
        if existing != expected_entry:
            fail("E_CONFLICT", "existing xpan_mode is not the verified native-photo clone")
        return data
    top = parse_wire(data)
    outer = next(x for x in top if x.number == 2)
    camera_fields = parse_wire(outer.value)
    photo_field = None
    boundary = len(camera_fields)
    for i, field in enumerate(camera_fields):
        if field.number != 1:
            continue
        entry = decode_message(field.value, "ModeEntry", pool)
        if entry["key"] == "photo_mode":
            photo_field = field
        if entry["key"] == "common":
            boundary = i
    entry_fields = parse_wire(photo_field.value)
    key_fields = [x for x in entry_fields if x.number == 1]
    if len(key_fields) != 1:
        fail("E_SCHEMA", "explicit native photo mode key required")
    new_entry = b"".join(x.with_payload(b"xpan_mode") if x is key_fields[0] else x.raw for x in entry_fields)
    clone = photo_field.with_payload(new_entry)
    payload = b"".join(x.raw for x in camera_fields[:boundary]) + clone + b"".join(x.raw for x in camera_fields[boundary:])
    output = bounded(b"".join(x.with_payload(payload) if x is outer else x.raw for x in top))
    after = inspect_indexed_pb(output, descriptor, form)
    after_modes = after["camera_feature_table"]["modes"]
    added = [x for x in after_modes if x["key"] == "xpan_mode"]
    if added != [expected_entry]:
        fail("E_PRESERVATION", "native clone semantic verification failed")
    after["camera_feature_table"]["modes"] = [x for x in after_modes if x["key"] != "xpan_mode"]
    if after != before:
        fail("E_PRESERVATION", "unrelated PB semantics/unknown fields changed")
    return output


def decrypt_c16_evidence(envelope, library, platform):
    if platform != "C16" or sha256(bounded(library)) != C16_LIBRARY_SHA256:
        fail("E_SECURITY_PROFILE", "offset 0x7fb is proven only for the hash-pinned C16 stock library")
    bounded(envelope)
    if len(envelope) < 24 or (len(envelope) - 8) % 16 or envelope[:4] != bytes.fromhex("01013498") or envelope[-4:] != bytes.fromhex("606c3fff"):
        fail("E_ENVELOPE", "not the observed C16 envelope")
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        fail("E_CRYPTO", "cryptography is unavailable; no dependency is installed automatically")
    table = library[0x7fb:0x7fb + 32]
    key = bytes(x ^ 0x3f for x in table[::2])
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    plaintext = decryptor.update(envelope[4:-4]) + decryptor.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    if encryptor.update(plaintext) + encryptor.finalize() != envelope[4:-4]:
        fail("E_CRYPTO", "C16 decrypt/re-encrypt round trip failed")
    document = JsonDocument(plaintext, allow_c16_padding=True)
    return plaintext, document.obj


def encrypt_envelope(*_args, **_kwargs):
    fail("E_UNVERIFIED_ENVELOPE", "envelope length/checksum/trailer semantics are unverified; encrypted output is disabled")


def checked_file(base, entry):
    if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
        fail("E_MANIFEST", "invalid manifest file record")
    relative = Path(entry["path"])
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        fail("E_MANIFEST", "manifest paths must remain relative to its directory")
    path = base / relative
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.resolve().is_relative_to(base.resolve()):
        fail("E_MANIFEST", "symlink or escaping manifest input")
    digest = entry.get("sha256")
    size = entry.get("size")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or type(size) is not int or not 0 <= size <= MAX_PAYLOAD:
        fail("E_MANIFEST", "exact size and lowercase SHA-256 required")
    data = read_bounded(path)
    if len(data) != size or sha256(data) != digest:
        fail("E_HASH", "manifest size/hash mismatch: " + str(relative))
    return data, str(path.resolve())


def load_manifest(path):
    path = Path(path).absolute()
    if path.is_symlink():
        fail("E_AUTH", "manifest must be a regular caller-provided file, not a symlink")
    document = JsonDocument(read_bounded(path))
    obj = document.obj
    if not isinstance(obj, dict):
        fail("E_MANIFEST", "manifest must be an object")
    validation = obj.get("validation")
    if type(obj.get("manifest_version")) is not int or obj["manifest_version"] != 1 or obj.get("platform") not in ("C16", "C17") or obj.get("source_kind") != "stock-original" or obj.get("original_validated") is not True:
        fail("E_AUTH", "caller-provided validated stock-original manifest v1 required")
    if not isinstance(validation, dict) or not all(isinstance(validation.get(k), str) and validation[k].strip() for k in ("method", "reference")):
        fail("E_AUTH", "stock validation method and reference required (hashes alone do not authenticate origin)")
    allowed = {"camera_unit_config": "json", "oplus_camera_config.json": "json", "camera_unit_feature_config.protobuf": "indexed-pb", "oplus_camera_config": "c16-envelope", "libOplusSecurity.so": "library"}
    files = obj.get("files")
    if not isinstance(files, dict) or not files or files.keys() - allowed.keys():
        fail("E_MANIFEST", "unsupported or empty input set")
    payloads, paths = {}, {}
    for name, record in files.items():
        if not isinstance(record, dict) or record.get("format") != allowed[name]:
            fail("E_MANIFEST", "input format mismatch: " + name)
        payloads[name], paths[name] = checked_file(path.parent, record)
        if sha256(payloads[name]) in C16_OVERLAY_SHA256:
            fail("E_ORIGINAL", "known C16 patched overlay is not an original: " + name)
        if obj["platform"] == "C16":
            if name not in C16_STOCK_SHA256 or sha256(payloads[name]) != C16_STOCK_SHA256[name]:
                fail("E_ORIGINAL", "not the investigated C16 stock original (overlays/plaintext derivatives cannot authorize patching): " + name)
        elif allowed[name] in ("c16-envelope", "library"):
            fail("E_SECURITY_PROFILE", "C17 encryption profile is not verified")
        elif sha256(payloads[name]) in C16_STOCK_SHA256.values():
            fail("E_ORIGINAL", "C16 inputs cannot be relabelled as C17")
    schema = None
    if "camera_unit_feature_config.protobuf" in payloads:
        if obj["platform"] != "C16":
            fail("E_SCHEMA", "C17 native schema binding is unavailable; no C17 PB generation")
        schema_record = obj.get("schema")
        schema, paths["schema"] = checked_file(path.parent, schema_record)
        verify_schema(schema, schema_record.get("format"))
    return obj, payloads, paths, schema, sha256(document.data)


def provenance(old, new, changes, qualification):
    return {"old_sha256": sha256(old), "new_sha256": sha256(new), "old_size": len(old), "new_size": len(new), "semantic_changes": changes, "unrelated_semantics_preserved": True, "double_patch_same_bytes": True, "qualification": qualification}


def make_patch(manifest, payloads, schema, command, c16_crop=False):
    if c16_crop and manifest["platform"] != "C16":
        fail("E_CROP", "C16 geometry must not be used for C17")
    outputs, proofs = {}, {}
    for name, data in payloads.items():
        if command == "patch-json" and name in ("camera_unit_config", "oplus_camera_config.json"):
            patcher = patch_unit_json if name == "camera_unit_config" else patch_vendor_json
            new = patcher(data, c16_crop)
            if patcher(new, c16_crop) != new:
                fail("E_IDEMPOTENCE", "JSON double patch changed bytes")
            changes = semantic_diff(JsonDocument(data).obj, JsonDocument(new).obj)
            outputs[name] = new
            proofs[name] = provenance(data, new, changes, "offline JSON unit; not runtime/HAL validated")
        if command == "patch-pb" and name == "camera_unit_feature_config.protobuf":
            form = manifest["schema"]["format"]
            new = patch_indexed_pb(data, schema, form)
            if patch_indexed_pb(new, schema, form) != new:
                fail("E_IDEMPOTENCE", "PB double patch changed bytes")
            outputs[name] = new
            proofs[name] = provenance(data, new, [{"kind": "add", "path": ["camera_feature_table", "xpan_mode"], "recipe": "exact target-native photo_mode clone; existing pool and raw fields unchanged"}], "observed C16 clone, NOT a proven minimal runtime feature subset")
    if not outputs:
        if "oplus_camera_config" in payloads and command == "patch-json":
            encrypt_envelope()
        fail("E_INPUT", "no supported input for requested patch unit")
    return outputs, proofs


def publish_bundle(out_dir, outputs, report):
    """Reserve NEW caller directory; publish all files as one results-directory rename."""
    destination = Path(out_dir).absolute()
    parent = destination.parent
    if not parent.is_dir() or destination.exists() or destination.is_symlink():
        fail("E_OUTPUT", "out-dir must not exist; its parent must already exist")
    # Validate the complete output in memory before creating anything.
    report_bytes = json.dumps(report, ensure_ascii=False, indent=2, default=json_safe).encode("utf-8")
    all_files = {**outputs, "provenance.json": bounded(report_bytes)}
    if len(all_files) > 8:
        fail("E_OUTPUT", "too many outputs")
    for name, data in all_files.items():
        if Path(name).name != name or name in ("", ".", ".."):
            fail("E_OUTPUT", "flat output names required")
        bounded(data)
    stage = Path(tempfile.mkdtemp(prefix=".xpan-offline-", dir=parent))
    reserved = False
    try:
        for name, data in all_files.items():
            with (stage / name).open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        # Exclusive mkdir is the no-overwrite guard (also on POSIX).
        destination.mkdir()
        reserved = True
        os.rename(stage, destination / "results")
    except Exception:
        if reserved:
            try:
                destination.rmdir()  # never delete files belonging to anyone else
            except OSError:
                pass
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return str(destination / "results")


def json_safe(value):
    if isinstance(value, Decimal):
        return {"json_decimal": str(value)}
    raise TypeError("unsupported report value")


def analyze(manifest, payloads, schema, comparison_dir=None):
    report = {"status": "analysis-only", "platform": manifest["platform"], "encrypted_output_supported": False, "c17_generation_supported": False, "files": {}}
    library = payloads.get("libOplusSecurity.so")
    for name, data in payloads.items():
        item = {"sha256": sha256(data), "size": len(data)}
        if name == "camera_unit_config":
            obj = JsonDocument(data).obj
            candidate = JsonDocument(patch_unit_json(data)).obj
            item.update(native_xpan_operation_mode=obj.get("mode_operation_mode", {}).get("xpan_mode"), entry_only_delta=semantic_diff(obj, candidate), rear_main_modes=obj.get("mode_type_list", {}).get("rear_main"))
            if manifest["platform"] == "C16":
                crop_candidate = patch_unit_json(data, c16_crop=True)
                item["observed_c16_main_crop_delta"] = semantic_diff(obj, JsonDocument(crop_candidate).obj)
                item["observed_c16_main_crop_double_patch_same_bytes"] = patch_unit_json(crop_candidate, c16_crop=True) == crop_candidate
        elif name == "oplus_camera_config.json":
            obj = JsonDocument(data).obj
            plain_for_proposal = data
        elif name == "oplus_camera_config":
            if library is None:
                fail("E_INPUT", "C16 encrypted analysis requires manifest-pinned libOplusSecurity.so")
            plaintext, obj = decrypt_c16_evidence(data, library, manifest["platform"])
            document = JsonDocument(plaintext, allow_c16_padding=True)
            _, json_end = document.spans[()]
            plain_for_proposal = document.text[:json_end].encode("utf-8")
            item["envelope_warning"] = "round-trip proven, but header/trailer semantics not proven; copying them into changed payload is disabled"
        elif name == "camera_unit_feature_config.protobuf":
            obj = inspect_indexed_pb(data, schema, manifest["schema"]["format"])
            clone = patch_indexed_pb(data, schema, manifest["schema"]["format"])
            item.update(string_pool_count=len(obj["str_pool"]), modes=[x["key"] for x in obj["camera_feature_table"]["modes"]], native_clone_sha256=sha256(clone), native_clone_size=len(clone), native_clone_double_patch_same_bytes=patch_indexed_pb(clone, schema, manifest["schema"]["format"]) == clone)
        else:
            report["files"][name] = item
            continue
        if name in ("oplus_camera_config", "oplus_camera_config.json"):
            records, tags = vendor_records(obj)
            item["vendor_record_count"] = len(records)
            item["xpan_records"] = {tag: [record for _, record in matches] for tag, matches in tags.items() if "xpan" in tag}
            item["duplicate_vendor_tags"] = {tag: len(matches) for tag, matches in tags.items() if len(matches) > 1}
            proposal = patch_vendor_json(plain_for_proposal, c16_crop=manifest["platform"] == "C16")
            item["main_only_plaintext_proposal"] = {
                "sha256": sha256(proposal),
                "semantic_delta": semantic_diff(obj, JsonDocument(proposal).obj),
                "double_patch_same_bytes": patch_vendor_json(proposal, c16_crop=manifest["platform"] == "C16") == proposal,
                "qualification": "observed C16 main crops or existing native support only; not runtime-minimality proof; not encrypted/written",
            }
        if comparison_dir is not None:
            comparison = read_bounded(Path(comparison_dir) / name)
            if name == "oplus_camera_config":
                _, new_obj = decrypt_c16_evidence(comparison, library, manifest["platform"])
            elif name == "camera_unit_feature_config.protobuf":
                new_obj = inspect_indexed_pb(comparison, schema, manifest["schema"]["format"])
                item["comparison_equals_native_clone_bytes"] = comparison == clone
                item["comparison_pool_identical"] = new_obj["str_pool"] == obj["str_pool"]
            else:
                new_obj = JsonDocument(comparison).obj
            item.update(comparison_sha256=sha256(comparison), semantic_delta=semantic_diff(obj, new_obj))
            if name in ("oplus_camera_config", "oplus_camera_config.json"):
                _, old_tags = vendor_records(obj)
                _, new_tags = vendor_records(new_obj)
                # This projection is additional descriptive evidence ONLY; full
                # ordered semantic_delta above remains authoritative.
                item["tag_delta"] = semantic_diff({k: [record for _, record in matches] for k, matches in old_tags.items()}, {k: [record for _, record in matches] for k, matches in new_tags.items()})
        report["files"][name] = item
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("analyze", "patch-json", "patch-pb"))
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--comparison-dir")
    parser.add_argument("--out-dir")
    parser.add_argument("--c16-crop", action="store_true", help="explicit observed main-only 4096x1512 recipe, not HAL validation")
    args = parser.parse_args(argv)
    try:
        manifest, payloads, paths, schema, manifest_hash = load_manifest(args.manifest)
        if args.command == "analyze":
            if args.out_dir or args.c16_crop:
                fail("E_ARGUMENT", "analyze never writes output or applies a crop")
            report = analyze(manifest, payloads, schema, args.comparison_dir)
            report.update(manifest_sha256=manifest_hash, caller_attestation=manifest["validation"], input_paths=paths)
        else:
            if not args.out_dir or args.comparison_dir:
                fail("E_ARGUMENT", "patch units require a new out-dir and do not accept comparison donors")
            outputs, proofs = make_patch(manifest, payloads, schema, args.command, args.c16_crop)
            report = {"status": "offline-patch-unit-only", "platform": manifest["platform"], "manifest_sha256": manifest_hash, "caller_attestation": manifest["validation"], "input_paths": paths, "files": proofs, "schema_sha256": SCHEMA_SHA256 if schema else None, "not_a_complete_config_set": True, "encrypted_envelope_not_generated": True, "c17_geometry_unknown": True}
            report["output_directory"] = publish_bundle(args.out_dir, outputs, report)
        print(json.dumps(report, ensure_ascii=False, indent=2, default=json_safe))
        return 0
    except (ConfigError, OSError) as exc:
        print(json.dumps({"status": "error", "code": getattr(exc, "code", "E_IO"), "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
