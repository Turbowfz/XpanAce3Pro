"""Read selected EROFS metadata directly from a device, without mounting or image dumping.

Only flat/inline files are extracted. Compressed inode layouts are reported, not guessed.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess

MODULE = Path(__file__).with_name("payload_range_reader.py")
spec = importlib.util.spec_from_file_location("xpan_payload_ranges", MODULE)
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


class DeviceRanges:
    def __init__(self, adb, serial, device, budget):
        if not re.fullmatch(r"/dev/block/dm-[0-9]+", device):
            raise ValueError("Only an explicitly selected dm block device is accepted")
        self.command = [str(adb), "-s", serial, "exec-out", "su", "-c"]
        self.device = device
        self.size = int(self.shell("blockdev --getsize64 " + device).decode().strip())
        self.budget = budget
        self.cache = {}
        self.transferred = 0
        self.audit = {"device": device, "size": self.size, "ranges": []}

    def shell(self, command):
        return subprocess.run(self.command + [command], check=True, capture_output=True,
                              timeout=30).stdout

    def read_at(self, offset, length, reason="metadata"):
        if offset < 0 or length < 0 or length > 8 * 1024 * 1024 or offset + length > self.size:
            raise ValueError("Unsafe read bounds")
        first, last = offset // 4096, (offset + length + 4095) // 4096
        missing = [block for block in range(first, last) if block not in self.cache]
        cursor = 0
        while cursor < len(missing):
            start = missing[cursor]
            end = start + 1
            cursor += 1
            while cursor < len(missing) and missing[cursor] == end and end - start < 256:
                end += 1
                cursor += 1
            count = end - start
            if self.transferred + count * 4096 > self.budget:
                raise ValueError("Device byte budget exceeded")
            command = f"dd if={self.device} bs=4096 skip={start} count={count} 2>/dev/null"
            data = self.shell(command)
            if len(data) != count * 4096:
                raise ValueError("Truncated block read; no inferred data")
            self.transferred += len(data)
            self.audit["ranges"].append({"offset": start * 4096, "length": len(data), "reason": reason})
            for index in range(count):
                self.cache[start + index] = data[index * 4096:(index + 1) * 4096]
        data = b"".join(self.cache[block] for block in range(first, last))
        return data[offset % 4096:offset % 4096 + length]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", required=True, type=Path)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--inspect", action="append", default=[])
    parser.add_argument("--extract-flat", action="store_true")
    parser.add_argument("--budget", type=int, default=4 * 1024 * 1024)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output must be a new directory")
    args.output.mkdir(parents=True)
    ranges = DeviceRanges(args.adb, args.serial, args.device, args.budget)
    fs = reader.TargetedErofs(ranges)
    inventory = []
    try:
        for path in args.inspect:
            inode = fs.resolve(path)
            item = {"path": path, "inode": inode}
            if args.extract_flat and inode["readable"] and inode["type"] == "file":
                data = fs.flat(inode, 8 * 1024 * 1024, path)
                output = args.output / Path(path).name
                with output.open("xb") as stream:
                    stream.write(data)
                item["sha256"] = hashlib.sha256(data).hexdigest()
            inventory.append(item)
            print(json.dumps(item, ensure_ascii=False))
    finally:
        ranges.audit["transferred_bytes"] = ranges.transferred
        (args.output / "read-audit.json").write_text(json.dumps(ranges.audit, indent=2), encoding="utf-8")
        (args.output / "inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
        print("Device blocks transferred:", ranges.transferred)


if __name__ == "__main__":
    main()
