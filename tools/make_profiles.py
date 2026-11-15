#!/usr/bin/env python3
"""Hash-qualified offline PJX110 profiles, never a phone installer.

Run with python -I. build requires exact stock trios and the investigated C17
SDK/app and decoded consumers. NEW output contains payload/profiles/{c16,c17},
originals/{c16,c17}, and a non-secret manifest.json. Firmware stays out of git.
C16's encrypted Vendor is copied unchanged: its envelope is not proven writable.
Generation/UI/crop getters for that profile must be supplied by the parent hook.
8192 final output, 1x-only, watermark, and XDR >=8 are runtime policies, NOT a
claim that these native 4096x1512 profiles implement or HAL-test those policies.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

# Load only our sibling source, never modules next to firmware or from cwd.
_spec = importlib.util.spec_from_file_location("_xpan_config_patch", Path(__file__).with_name("config_patch.py"))
cp = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cp
_spec.loader.exec_module(cp)

FILES = ("camera_unit_config", "camera_unit_feature_config.protobuf", "oplus_camera_config")
SUPPORT = cp.SUPPORT
PICTURE = cp.PICTURE
VERSION = "com.oplus.xpan.mode.version"
LEGACY = "com.oplus.xpan.legacy.ui.style"
CROPS = copy.deepcopy(cp.C16_MAIN_CROPS)
STOCK = {
    "c16": {
        FILES[0]: (18732, cp.C16_STOCK_SHA256[FILES[0]]),
        FILES[1]: (36882, cp.C16_STOCK_SHA256[FILES[1]]),
        FILES[2]: (38008, cp.C16_STOCK_SHA256[FILES[2]]),
    },
    "c17": {
        FILES[0]: (18732, "2adeaa2e3af2657e988ac35d62fc53f5fe795276e204e857d6e9d7738634ad21"),
        FILES[1]: (36882, "e3b0144756531f57227e6e197dddddbc6f13dc1553f727aca383176f23255a58"),
        FILES[2]: (42828, "a264cf97b3b267fb80d916d84030cdd90beeb3c786dad75dfb5e5637f389d852"),
    },
}
OUTPUTS = {
    "c16": {
        FILES[0]: (18732, "6dff1eaf085af1e61817eda9c34e4937e67e2ef4416cbee31409051e43ce39f7"),
        FILES[1]: (40573, "baab74e1dae8501a0ecf2c9d75847a64cfbb2afee91945e2311edb9e1caedd2e"),
        FILES[2]: STOCK["c16"][FILES[2]],
    },
    "c17": {
        FILES[0]: (18732, "6dff1eaf085af1e61817eda9c34e4937e67e2ef4416cbee31409051e43ce39f7"),
        FILES[1]: (40573, "baab74e1dae8501a0ecf2c9d75847a64cfbb2afee91945e2311edb9e1caedd2e"),
        FILES[2]: (43249, "5d2002fddbfe4d21bb446081e84c30a5bb96261edb69c21bd2617e2f489424ff"),
    },
}
TARGETS = {
    "c16": {
        "camera_version": "6.020.876",
        "rom_build": "PJX110 C16 baseline",
        "fingerprint": "OnePlus/PJX110/OP5D06L1:16/UKQ1.231108.001/U.26f642a-3a0080-3a0082:user/release-keys",
    },
    "c17": {
        "camera_version": "7.013.51",
        "rom_build": "PJX110_17.0.1.201(CN01)",
        "fingerprint": "OnePlus/PJX110/OP5D06L1:17/CP2A.260605.016/C.3601857-f47121-f4775e:user/release-keys",
    },
}
# These are evidence pins, not a trust-on-first-use manifest or donor firmware.
SDK_PINS = {
    "c16": {"sdk_sha256": "37a13d408f7ee95b8e954c2ef11c44a0aee433da45958cc7ddb80c688e0692c6",
            "adapter_sha256": "ac0b595faae8d3f684aa457f223fbfb0f167f0d0e0cc461ef08dbbe52d6e61cf"},
    "c17": {"sdk_sha256": "d2b20133252414e26ac7c68255c76fa61cf41826c3b63d423a14c1187af42899",
            "adapter_sha256": "7418d59955746d8aca56438a5e0e26e1f08ff6ae4d0cacc22057d0041b12aac4"},
}
EVIDENCE = {
    "sdk_jar": (5377971, "d2b20133252414e26ac7c68255c76fa61cf41826c3b63d423a14c1187af42899"),
    "camera_apk": (171164534, "c953552336abb6868f9c60194fdb76391e7a20c229cfdf477fe9358f03bd9add"),
    "schema": (40904, "a3384b7b2b49be62f5f7eef34db1b864243b68d49bc1bcaae1cee30779fff4b9"),
    "xpan_model": (3772, "b49fda6bac75a41282ae0d59effaec19cf7f12f321a695548b6d1fe9e3bc3628"),
    "crop_consumer": (34003, "e8467b17e2504de83f4a766ff4d814275c680d64a700afebb9e99d635d72077b"),
    "camera_config": (69089, "e1f5098030d68dfab02d7f758949d94b1e996c7497eea8f272f4c55e27d9c34d"),
}
DECODED = {
    "schema": "sdk/smali/com/oplus/ocs/camera/configure/ProtobufFeatureConfig.smali",
    "xpan_model": "camera/smali_classes7/cl/b.smali",
    "crop_consumer": "camera/smali_classes6/al/w.smali",
    "camera_config": "camera/smali/com/oplus/camera/configure/CameraConfig.smali",
}


def fail(code, message):
    cp.fail(code, message)


def regular_path(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        fail("E_INPUT", "regular non-symlink input required")
    return path


def read_pinned(path, pin):
    path = regular_path(path)
    size, digest = pin
    if path.stat().st_size != size:
        fail("E_ORIGINAL", "input size is not the exact investigated original: " + path.name)
    data = cp.read_bounded(path)
    if cp.sha256(data) != digest:
        fail("E_ORIGINAL", "input SHA-256 is not the exact investigated original: " + path.name)
    return data


def hash_large_pinned(path, pin):
    path = regular_path(path)
    if path.stat().st_size != pin[0]:
        fail("E_EVIDENCE", "SDK/app evidence size mismatch")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != pin[1]:
        fail("E_EVIDENCE", "SDK/app evidence hash mismatch")


def validate_evidence(decoded_dir, sdk_jar, camera_apk):
    hash_large_pinned(sdk_jar, EVIDENCE["sdk_jar"])
    hash_large_pinned(camera_apk, EVIDENCE["camera_apk"])
    data = {name: read_pinned(Path(decoded_dir) / relative, EVIDENCE[name])
            for name, relative in DECODED.items()}
    cp.verify_schema(data["schema"], "smali")
    # Hashes bind these signatures to the actually investigated 7.013.51 app.
    model = data["xpan_model"].decode("utf-8")
    config = data["camera_config"].decode("utf-8")
    consumer = data["crop_consumer"].decode("utf-8")
    required = (
        VERSION in model and LEGACY in model,
        "->m(ILjava/lang/String;)I" in model,
        "->d(Ljava/lang/String;ZZ)Z" in model,
        "const/4 v0, 0x3" in model and "if-ne v0, v1" in model,
        ".method public static m(ILjava/lang/String;)I" in config,
        "Ljava/lang/Integer;->parseInt(Ljava/lang/String;)I" in config,
        "->getConfigIntArrayValue(Ljava/lang/String;)[I" in config,
        all(x["VendorTag"] in consumer for x in CROPS),
        "->l(Ljava/lang/String;[I)[I" in consumer,
        "Landroid/graphics/Point;-><init>(II)V" in consumer,
        "Landroid/util/Size;-><init>(II)V" in consumer,
    )
    if not all(required):
        fail("E_EVIDENCE", "generation/crop Java type or consumer proof missing")
    return data["schema"]


def patch_vendor(data):
    """C17 plaintext only: scalar flags plus known offset,width,height crops."""
    # Shared low-level recipe is named C16 in config_patch; calling it here is
    # justified by exact common stock unit/PB bytes AND verified C17 consumers.
    output = cp.patch_vendor_json(data, c16_crop=True)
    doc = cp.JsonDocument(output)
    records, tags = cp.vendor_records(doc.obj)
    additions = (
        {"VendorTag": VERSION, "Type": "Int32", "Count": "1", "Value": "3"},
        {"VendorTag": LEGACY, "Type": "Byte", "Count": "1", "Value": "0"},
    )
    missing = []
    for record in additions:
        name = record["VendorTag"]
        if name in tags:
            _, existing = cp.unique_vendor(tags, name)
            if any(existing.get(k) != v for k, v in record.items()):
                fail("E_CONFLICT", "unverified existing generation/legacy record: " + name)
        else:
            missing.append(record)
    if not missing:
        return output
    expected = copy.deepcopy(doc.obj)
    expected["file_data"].extend(copy.deepcopy(missing))
    _, end = doc.spans[("file_data",)]
    insert = ("," if records else "") + "\n" + ",\n".join(json.dumps(x) for x in missing)
    result = cp.bounded((doc.text[:end - 1] + insert + doc.text[end - 1:]).encode("utf-8"))
    if cp.JsonDocument(result).obj != expected:
        fail("E_PRESERVATION", "Vendor unrelated semantics changed")
    return result


def getter_overrides():
    """Required only for C16's byte-identical encrypted Vendor; key-scoped."""
    return [
        {"key": SUPPORT, "java_type": "boolean", "value": True},
        {"key": VERSION, "java_type": "int", "value": 3},
        {"key": LEGACY, "java_type": "boolean", "value": False},
        {"key": PICTURE, "java_type": "size", "value": [4096, 1512]},
        *[{"key": x["VendorTag"], "java_type": "int[]",
           "value": [int(v) for v in x["Value"].split(",")]} for x in CROPS],
    ]


def make_profile(platform, originals, schema):
    if platform not in STOCK or set(originals) != set(FILES):
        fail("E_PROFILE", "exact supported profile and complete three-file set required")
    for name in FILES:
        if (len(originals[name]), cp.sha256(originals[name])) != STOCK[platform][name]:
            fail("E_ORIGINAL", "stock SHA pins cannot be bypassed by a version/label/manifest")
    cp.verify_schema(schema, "smali")
    output = {
        FILES[0]: cp.patch_unit_json(originals[FILES[0]], c16_crop=True),
        FILES[1]: cp.patch_indexed_pb(originals[FILES[1]], schema, "smali"),
        FILES[2]: originals[FILES[2]] if platform == "c16" else patch_vendor(originals[FILES[2]]),
    }
    for name in FILES:
        if (len(output[name]), cp.sha256(output[name])) != OUTPUTS[platform][name]:
            fail("E_RECIPE", "output differs from this exact versioned profile recipe")
    if cp.patch_unit_json(output[FILES[0]], True) != output[FILES[0]]:
        fail("E_IDEMPOTENCE", "unit double patch changed bytes")
    if cp.patch_indexed_pb(output[FILES[1]], schema, "smali") != output[FILES[1]]:
        fail("E_IDEMPOTENCE", "PB double patch changed bytes")
    if platform == "c17" and patch_vendor(output[FILES[2]]) != output[FILES[2]]:
        fail("E_IDEMPOTENCE", "Vendor double patch changed bytes")
    proofs = {}
    for name in FILES:
        old, new = originals[name], output[name]
        if name == FILES[1]:
            changes = cp.semantic_diff(cp.inspect_indexed_pb(old, schema, "smali"),
                                       cp.inspect_indexed_pb(new, schema, "smali"))
        elif name == FILES[2] and platform == "c16":
            changes = []
        else:
            changes = cp.semantic_diff(cp.JsonDocument(old).obj, cp.JsonDocument(new).obj)
        proofs[name] = {
            "original": {"size": len(old), "sha256": cp.sha256(old), "path": f"originals/{platform}/{name}"},
            "output": {"size": len(new), "sha256": cp.sha256(new), "path": f"payload/profiles/{platform}/{name}"},
            "install_allowed_sha256": sorted({cp.sha256(old), cp.sha256(new)}),
            "unrelated_semantics_preserved": True,
            "double_patch_same_bytes": True,
            "semantic_changes": changes,
        }
    receipt = {
        **TARGETS[platform], "profile_id": platform, "files": proofs,
        "native_picture_size": [4096, 1512],
        "required_java_getter_overrides": getter_overrides() if platform == "c16" else [],
        "encrypted_vendor_unchanged": platform == "c16",
        "native_binary_replacements": [],
        "provenance": {
            "stock_source": "PJX110 C16 local stock extraction" if platform == "c16" else
                            "PJX110 C17 original ODM EROFS, exact-index extraction stock-20261114-05",
            "unit_pb_shared_original_bytes": True,
            "schema_sha256": cp.SCHEMA_SHA256,
            "pb_recipe": "exact target-native photo_mode clone; pool indices and other wire fields unchanged; no donor",
            "crop_recipe": "C16 observed offset/size geometry reused on same PJX110; actual C17 CameraConfig.l int[] and Point/Size consumers verified",
            "generation_recipe": "actual 7.013.51 cl.b b() legacy boolean, version integer; c() compares integer 3",
            "hal_tested": False,
        },
    }
    return output, receipt


def qualifies(receipt, camera_version, fingerprint, observed_sha256):
    """Whole-trio gate, never version-only and never a caller-invented stock pin.

    The receipt must be the build's own trusted manifest (not target/donor data).
    All three files must be original or THIS profile's own output, not any old
    release/donor. Hooks/native library hashes cannot substitute for these pins.
    """
    if not isinstance(receipt, dict) or receipt.get("profile_id") not in STOCK:
        return False
    platform = receipt["profile_id"]
    target = TARGETS[platform]
    if camera_version != target["camera_version"] or fingerprint != target["fingerprint"]:
        return False
    if receipt.get("camera_version") != camera_version or receipt.get("fingerprint") != fingerprint:
        return False
    if not isinstance(observed_sha256, dict) or set(observed_sha256) != set(FILES):
        return False
    try:
        for name in FILES:
            record = receipt["files"][name]
            original = record["original"]
            if (original["size"], original["sha256"]) != STOCK[platform][name]:
                return False
            output = record["output"]
            if (output["size"], output["sha256"]) != OUTPUTS[platform][name]:
                return False
            allowed = {original["sha256"], output["sha256"]}
            if sorted(allowed) != record["install_allowed_sha256"] or observed_sha256[name] not in allowed:
                return False
    except (KeyError, TypeError):
        return False
    return True


def profile_props(platform, receipt):
    props = {"profile_id": platform, "model": "PJX110",
             "camera_version": receipt["camera_version"],
             "build_fingerprint": receipt["fingerprint"]}
    for short, name in zip(("unit", "feature", "vendor"), FILES):
        for kind in ("original", "output"):
            props[f"{kind}_{short}_sha256"] = receipt["files"][name][kind]["sha256"]
    props.update(SDK_PINS[platform])
    return ("\n".join(f"{key}={value}" for key, value in props.items()) + "\n").encode("ascii")


def asset_receipt(platform, receipt):
    return {"profile_id": platform, "model": "PJX110",
            "camera_version": receipt["camera_version"],
            "build_fingerprint": receipt["fingerprint"],
            **SDK_PINS[platform],
            "files": {name: {kind: record[kind] for kind in
                              ("original", "output", "install_allowed_sha256")}
                      for name, record in receipt["files"].items()},
            "required_java_getter_overrides": receipt["required_java_getter_overrides"],
            "native_picture_size": [4096, 1512], "hal_tested": False}


def publish(out_dir, profiles, originals, receipts):
    destination = Path(out_dir).absolute()
    parent = destination.parent
    if not parent.is_dir() or destination.exists() or destination.is_symlink():
        fail("E_OUTPUT", "output must be a new directory with an existing parent")
    if any(p.is_symlink() for p in (parent, *parent.parents)):
        fail("E_OUTPUT", "output must not traverse symlinks")
    if set(profiles) != set(STOCK) or set(originals) != set(STOCK) or set(receipts) != set(STOCK):
        fail("E_OUTPUT", "both exact profiles are required")
    for platform in STOCK:
        if set(profiles[platform]) != set(FILES) or set(originals[platform]) != set(FILES):
            fail("E_OUTPUT", "only the qualified three filenames may be published")
        for name in FILES:
            old, new = cp.bounded(originals[platform][name]), cp.bounded(profiles[platform][name])
            if (len(old), cp.sha256(old)) != STOCK[platform][name]:
                fail("E_ORIGINAL", "publication original is not stock")
            record = receipts[platform]["files"][name]
            if record["output"]["size"] != len(new) or record["output"]["sha256"] != cp.sha256(new):
                fail("E_OUTPUT", "publication output does not match its receipt")
        observed = {n: cp.sha256(b) for n, b in profiles[platform].items()}
        if not qualifies(receipts[platform], TARGETS[platform]["camera_version"],
                         TARGETS[platform]["fingerprint"], observed):
            fail("E_OUTPUT", "publication receipt does not qualify")
    manifest = {
        "manifest_version": 1, "recipe_id": "PJX110-Xpan-v2-native-crop-v1",
        "module_version": "2.0", "module_version_code": 20,
        "profiles": receipts,
        "sdk_app_evidence": {name: {"size": pin[0], "sha256": pin[1]} for name, pin in EVIDENCE.items()},
        "runtime_policies_not_implemented_by_profiles": {
            "final_output_width": 8192, "zoom_ratios": [1.0],
            "hasselblad_ui": True, "watermark": True, "xdr_ratio_minimum": 8.0,
        },
        "qualification": "exact camera version + exact ROM fingerprint + complete original-or-own-output SHA trio; inactive on mismatch",
        "hal_tested": False,
    }
    manifest_bytes = cp.bounded(json.dumps(manifest, ensure_ascii=False, indent=2,
                                           default=cp.json_safe).encode("utf-8"))
    stage = Path(tempfile.mkdtemp(prefix=".xpan-profiles-", dir=parent))
    reserved = False
    try:
        for category, collection in (("payload/profiles", profiles), ("originals", originals)):
            for platform, files in collection.items():
                folder = stage / category / platform
                folder.mkdir(parents=True)
                for name, data in files.items():
                    with (folder / name).open("xb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
        for platform, receipt in receipts.items():
            (stage / "payload/profiles" / platform / "profile.props").write_bytes(profile_props(platform, receipt))
            assets = stage / "profiles" / platform
            assets.mkdir(parents=True)
            (assets / "receipt.json").write_text(json.dumps(asset_receipt(platform, receipt), indent=2), encoding="utf-8")
        with (stage / "manifest.json").open("xb") as stream:
            stream.write(manifest_bytes)
        # Exclusive reservation is the no-overwrite guard on POSIX and Windows.
        destination.mkdir()
        reserved = True
        for child in stage.iterdir():
            os.rename(child, destination / child.name)
        return destination
    except Exception:
        if reserved:
            # Remove only our reservation/artifacts, not a pre-existing caller dir.
            shutil.rmtree(destination)
        raise
    finally:
        shutil.rmtree(stage)


def build(c16_stock_dir, c17_stock_dir, decoded_dir, sdk_jar, camera_apk, out_dir):
    # Reject output reuse before reading or mutating any firmware.
    if Path(out_dir).exists() or Path(out_dir).is_symlink():
        fail("E_OUTPUT", "output directory already exists")
    schema = validate_evidence(decoded_dir, sdk_jar, camera_apk)
    originals = {}
    profiles, receipts = {}, {}
    for platform, directory in (("c16", c16_stock_dir), ("c17", c17_stock_dir)):
        originals[platform] = {name: read_pinned(Path(directory) / name, STOCK[platform][name]) for name in FILES}
        profiles[platform], receipts[platform] = make_profile(platform, originals[platform], schema)
    return publish(out_dir, profiles, originals, receipts)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--c16-stock-dir", required=True)
    parser.add_argument("--c17-stock-dir", required=True)
    parser.add_argument("--c17-decoded-dir", required=True)
    parser.add_argument("--c17-sdk-jar", required=True)
    parser.add_argument("--c17-camera-apk", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)
    try:
        result = build(args.c16_stock_dir, args.c17_stock_dir, args.c17_decoded_dir,
                       args.c17_sdk_jar, args.c17_camera_apk, args.out_dir)
    except (cp.ConfigError, OSError) as exc:
        print(f"{getattr(exc, 'code', 'E_IO')}: {exc}", file=sys.stderr)
        return 2
    print(str(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
