#!/usr/bin/env python3
"""深入分析USBPcap包结构，定位打印数据流"""
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
    if block_type == 0x00000006:  # EPB
        if_id, ts_h, ts_l, cap_len, orig_len = struct.unpack_from("<IIIII", data, pos + 8)
        pkt = data[pos + 28 : pos + 28 + cap_len]
        packets.append(pkt)
    pos += block_len

print(f"总包数: {len(packets)}")

def parse(pkt):
    if len(pkt) < 28:
        return None
    header_len = struct.unpack_from("<H", pkt, 0)[0]
    irp_id = struct.unpack_from("<Q", pkt, 2)[0]
    status = struct.unpack_from("<I", pkt, 10)[0]
    function = struct.unpack_from("<H", pkt, 14)[0]
    info = pkt[16]
    bus, device = struct.unpack_from("<HH", pkt, 17)
    endpoint = pkt[21]
    transfer = pkt[22]
    data_len = struct.unpack_from("<I", pkt, 23)[0]
    extra = pkt[27] if header_len > 27 else None
    payload = pkt[header_len:]
    return dict(hlen=header_len, irp=irp_id, status=status, func=function, info=info,
                bus=bus, dev=device, ep=endpoint, xfer=transfer, dlen=data_len,
                extra=extra, payload=payload)

# 28字节头: hlen(2) irpId(8) status(4) func(2) info(1) bus(2) dev(2) ep(1) xfer(1) dlen(4) extra(1)
# 注意: 旧版USBPcap是27字节头，新版28字节

parsed = [p for p in (parse(pkt) for pkt in packets) if p]

# 统计各 (bus,dev) 设备
devs = {}
for p in parsed:
    key = (p["bus"], p["dev"])
    devs.setdefault(key, []).append(p)

for key, plist in sorted(devs.items()):
    funcs = {}
    for p in plist:
        funcs[p["func"]] = funcs.get(p["func"], 0) + 1
    print(f"\n设备 bus={key[0]} dev={key[1]}: {len(plist)}包, functions={funcs}")

# 找包含 @PJL 或 PCL 转义序列的包
print("\n===== 包含PJL/PCL特征的包 =====")
for i, p in enumerate(parsed):
    pl = p["payload"]
    if b"@PJL" in pl[:200] or b"\x1b%-12345X" in pl[:200] or b"\x1bE" in pl[:20] or b"\x1b&l" in pl[:50]:
        print(f"包{i}: bus={p['bus']} dev={p['dev']} ep=0x{p['ep']:02x} info=0x{p['info']:02x} "
              f"xfer={p['xfer']} dlen={p['dlen']} func=0x{p['func']:04x} status=0x{p['status']:08x}")
        printable = "".join(chr(b) if 32 <= b < 127 else "." for b in pl[:120])
        print(f"  payload前120字节: {printable}")

# 打印所有 bulk (xfer=3) 包的概要
print("\n===== BULK包概要 (前40个) =====")
count = 0
for i, p in enumerate(parsed):
    if p["xfer"] == 3:
        count += 1
        if count <= 40:
            pl = p["payload"][:32]
            printable = "".join(chr(b) if 32 <= b < 127 else "." for b in pl)
            print(f"包{i}: bus={p['bus']} dev={p['dev']} ep=0x{p['ep']:02x} info=0x{p['info']:02x} "
                  f"dlen={p['dlen']} caplen={len(p['payload'])} | {printable}")
print(f"BULK包总数: {count}")
