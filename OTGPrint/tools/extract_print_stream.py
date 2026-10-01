#!/usr/bin/env python3
"""提取打印数据流并分析PCL3-GUI格式（尽力提取，注意截断）"""
import struct

path = "/Users/apple/Desktop/项目/OTGPrint/huipu.pcapng"

with open(path, "rb") as f:
    data = f.read()

pos = 0
packets = []
while pos + 12 <= len(data):
    block_type, block_len = struct.unpack_from("<II", data, pos)
    if block_len < 12 or pos + block_len > len(data):
        break
    if block_type == 0x00000006:
        if_id, ts_h, ts_l, cap_len, orig_len = struct.unpack_from("<IIIII", data, pos + 8)
        pkt = data[pos + 28 : pos + 28 + cap_len]
        packets.append((pkt, orig_len))
    pos += block_len

def parse(pkt):
    if len(pkt) < 28:
        return None
    hlen = struct.unpack_from("<H", pkt, 0)[0]
    irp = struct.unpack_from("<Q", pkt, 2)[0]
    func = struct.unpack_from("<H", pkt, 14)[0]
    info = pkt[16]
    bus, dev = struct.unpack_from("<HH", pkt, 17)
    ep = pkt[21]
    xfer = pkt[22]
    dlen = struct.unpack_from("<I", pkt, 23)[0]
    payload = pkt[hlen:]
    return dict(irp=irp, func=func, info=info, bus=bus, dev=dev, ep=ep,
                xfer=xfer, dlen=dlen, payload=payload)

# 提取所有 func=9 (BULK_OR_INTERRUPT) ep=0x08 info=0 的打印数据
chunks = []
for i, (pkt, orig_len) in enumerate(packets):
    p = parse(pkt)
    if not p:
        continue
    if p["func"] == 9 and p["ep"] == 0x08 and (p["info"] & 1) == 0:
        chunks.append((i, p, orig_len))

print(f"打印数据块数: {len(chunks)}")
total = 0
for i, p, orig_len in chunks:
    total += p["dlen"]
    print(f"  包{i}: dlen={p['dlen']} caplen={len(p['payload'])} truncated={p['dlen'] > len(p['payload'])}")
print(f"打印流总长度(理论): {total}")

# 第一个块的起始数据 — 任务头
first = chunks[0][1]
pl = first["payload"]
print("\n===== 打印流前512字节 hex =====")
print(pl[:512].hex(" "))
print("\n===== 打印流前512字节 可读 =====")
print("".join(chr(b) if 32 <= b < 127 else "." for b in pl[:512]))

# 搜索流中的PCL/PJL转义序列
print("\n===== 转义序列搜索 (前64KB) =====")
joined = pl
import re
# PCL: \x1b 后跟命令; PJL: @PJL
for m in re.finditer(rb"(@PJL[^\r\n]*)", joined[:65508]):
    print(f"@PJL @{m.start()}: {m.group(1)[:80]}")
escapes = re.finditer(rb"\x1b[\x20-\x7e][\x20-\x7e]?[\x20-\x7e]?[\x20-\x7e]?[\x20-\x7e]?[\x20-\x7e]?[A-Za-z]", joined[:4096])
seen = []
for m in escapes:
    s = m.group(0)
    seen.append((m.start(), s))
for off, s in seen[:80]:
    print(f"esc @{off}: {s}")

# 合并所有捕获的数据(标注截断)保存
out = bytearray()
for i, p, orig_len in chunks:
    out += p["payload"]
with open("/Users/apple/Desktop/项目/OTGPrint/reference_huipu_partial.bin", "wb") as f:
    f.write(out)
print(f"\n已保存捕获部分: {len(out)} 字节 (理论{total}, 完整度{len(out)*100//total}%)")

# 完整设备ID字符串
print("\n===== 完整1284设备ID =====")
for i, (pkt, orig_len) in enumerate(packets):
    p = parse(pkt)
    if not p:
        continue
    if p["func"] == 0x1B and p["info"] & 1 and len(p["payload"]) > 10:
        pl = p["payload"]
        idlen = (pl[0] << 8) | pl[1]
        print(pl[2:2+idlen-2].decode("ascii", errors="replace"))
        break
