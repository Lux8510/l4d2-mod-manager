"""生成用于测试冲突检测的合成 VPK(仅测试用,不随工具发布)。"""
import os, struct, sys, zlib

MAGIC = 0x55AA1234

def build_vpk(files):
    """files: dict path -> bytes. 生成单文件嵌入式 VPK v1 目录文件字节。"""
    grouped = {}  # ext -> dir -> name -> data
    for full, data in files.items():
        d, name = (full.rsplit("/", 1) if "/" in full else (" ", full))
        name, ext = name.rsplit(".", 1) if "." in name else (name, " ")
        grouped.setdefault(ext, {}).setdefault(d, {})[name] = data

    order = []  # list of (ext, dir, name, data) for data section
    for ext in sorted(grouped):
        for d in sorted(grouped[ext]):
            for name in sorted(grouped[ext][d]):
                order.append((ext, d, name, grouped[ext][d][name]))

    def serialize(offsets):
        out = bytearray()
        for ext in sorted(grouped):
            out += ext.encode() + b"\x00"
            for d in sorted(grouped[ext]):
                out += d.encode() + b"\x00"
                for name in sorted(grouped[ext][d]):
                    idx = order.index((ext, d, name, grouped[ext][d][name]))
                    data = grouped[ext][d][name]
                    out += name.encode() + b"\x00"
                    out += struct.pack("<IHHIIH", zlib.crc32(data) & 0xFFFFFFFF, 0, 0x7FFF, offsets[idx], len(data), 0xFFFF)
                out += b"\x00"  # end filenames of this dir
            out += b"\x00"      # end dirs of this ext
        out += b"\x00"          # end of extensions
        return bytes(out)

    tree = serialize([0] * len(order))
    tree_size = len(tree)
    offsets, cur = [], 0
    for (ext, d, name, data) in order:
        offsets.append(cur); cur += len(data)
    tree = serialize(offsets)
    assert len(tree) == tree_size, (len(tree), tree_size)
    header = struct.pack("<III", MAGIC, 1, tree_size)
    return header + tree + b"".join(e[3] for e in order)

def write(path, files):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(build_vpk(files))

def info(title):
    return ('addontitle  "%s"\r\naddonversion "1"\r\n' % title).encode("utf-8")

if __name__ == "__main__":
    root = sys.argv[1]
    addons = os.path.join(root, "left4dead2", "addons", "workshop")
    write(os.path.join(addons, "111111", "111111.vpk"), {
        "addoninfo.txt": info("AK47 皮肤包"),
        "materials/models/weapons/ak47/ak47_skin.vmt": b"A",
        "materials/models/weapons/ak47/ak47_skin.vtf": b"A",
        "scripts/weapon_ak47.txt": b"A",
        "materials/models/weapons/ak47/only_a.vtf": b"A",
    })
    # 模组B: 与A冲突3个(含scripts)
    write(os.path.join(addons, "222222", "222222.vpk"), {
        "addoninfo.txt": info("AK47 二次元皮肤"),
        "materials/models/weapons/ak47/ak47_skin.vmt": b"B",
        "materials/models/weapons/ak47/ak47_skin.vtf": b"B",
        "scripts/weapon_ak47.txt": b"B",
        "materials/models/weapons/ak47/only_b.vtf": b"B",
    })
    # 模组C: 无冲突
    write(os.path.join(addons, "333333", "333333.vpk"), {
        "addoninfo.txt": info("喷漆"),
        "materials/vgui/spray.vtf": b"C",
    })
    print("测试数据已生成:", root)
