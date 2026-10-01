#!/usr/bin/env python3
"""分析pcapng链路层类型和数据包结构"""
import struct

SHB = 0x0A0D0D0A
IDB = 0x00000001
EPB = 0x00000006
ISB = 0x00000005

path = "/Users/apple/Desktop/项目/OTGPrint/huipu.pcapng"

with open(path, "rb") as f:
    data = f.read()

pos = 0
linktypes = {}
packets = []
while pos + 12 <= len(data):
    block_type, block_len = struct.unpack_from("<II", data, pos)
    if block_len < 12 or pos + block_len > len(data):
        print(f"块异常: type=0x{block_type:08x} len={block_len} pos={pos}")
        break
    if block_type == IDB:
        # IDB: linktype(2) reserved(2) snaplen(4)
        lt = struct.unpack_from("<H", data, pos + 8)[0]
        linktypes[lt] = linktypes.get(lt, 0) + 1
    elif block_type == EPB:
        if_id, ts_h, ts_l, cap_len, orig_len = struct.unpack_from("<IIIII", data, pos + 8)
        pkt = data[pos + 28 : pos + 28 + cap_len]
        packets.append((if_id, pkt, orig_len))
    pos += block_len

print(f"链路层类型: {linktypes}")
print(f"数据包数: {len(packets)}")

# 打印前10个包的长度和前64字节
for i, (if_id, pkt, orig_len) in enumerate(packets[:10]):
    print(f"\n--- 包{i}: if={if_id} cap={len(pkt)} orig={orig_len}")
    print(pkt[:96].hex(" "))
    printable = "".join(chr(b) if 32 <= b < 127 else "." for b in pkt[:96])
    print(printable)

# 统计包长度分布
from collections import Counter
lens = Counter(len(p[1]) for p in packets)
print(f"\n包长度分布(前20): {lens.most_common(20)}")
