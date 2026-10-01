#!/usr/bin/env python3
"""分析打印机设备的控制传输(设备ID等)和打印数据流格式"""
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
    status = struct.unpack_from("<I", pkt, 10)[0]
    func = struct.unpack_from("<H", pkt, 14)[0]
    info = pkt[16]
    bus, dev = struct.unpack_from("<HH", pkt, 17)
    ep = pkt[21]
    xfer = pkt[22]
    dlen = struct.unpack_from("<I", pkt, 23)[0]
    payload = pkt[hlen:]
    return dict(irp=irp, status=status, func=func, info=info, bus=bus, dev=dev,
                ep=ep, xfer=xfer, dlen=dlen, payload=payload)

# 打印机设备 bus=1 dev=8 的所有包
print("===== 打印机(bus=1,dev=8)所有包明细 =====")
for i, (pkt, orig_len) in enumerate(packets):
    p = parse(pkt)
    if not p or p["bus"] != 1 or p["dev"] != 8:
        continue
    pl = p["payload"]
    desc = ""
    if p["func"] == 8 or p["func"] == 11:  # control
        if p["info"] == 0:  # request
            if len(pl) >= 8:
                bm, br, wv, wi, wl = struct.unpack_from("<BBHHH", pl, 0)
                desc = f"SETUP bm=0x{bm:02x} req=0x{br:02x} val=0x{wv:04x} idx=0x{wi:04x} len={wl}"
                # printer class: req=0 GET_DEVICE_ID, req=1 GET_PORT_STATUS, req=2 SOFT_RESET
                if br == 0 and bm == 0:
                    desc += " [GET_DEVICE_ID printer]"
                elif br == 1 and bm == 0x80:
                    desc += " [GET_PORT_STATUS]"
                elif br == 2 and bm == 0:
                    desc += " [SOFT_RESET]"
                elif br == 6 and bm == 0x80:
                    desc += " [GET_DESCRIPTOR]"
                elif br == 5 and bm == 0:
                    desc += " [SET_INTERFACE]"
                elif br == 1 and bm == 0:
                    desc += " [CLEAR_FEATURE]"
                elif br == 0 and bm == 0x80:
                    desc += " [GET_STATUS]"
        else:  # response
            printable = "".join(chr(b) if 32 <= b < 127 else "." for b in pl[:100])
            desc = f"DATA[{len(pl)}]: {printable}"
    elif p["func"] == 27:  # bulk/interrupt
        direction = "OUT" if (p["ep"] & 0x80) == 0 else "IN"
        # info bit: 1 = completion(PDO->FDO)
        phase = "完成" if p["info"] & 1 else "提交"
        desc = f"BULK/{direction} {phase} dlen={p['dlen']} caplen={len(pl)}"
        if pl:
            hexb = pl[:48].hex(" ")
            printable = "".join(chr(b) if 32 <= b < 127 else "." for b in pl[:48])
            desc += f"\n      hex: {hexb}\n      txt: {printable}"
    elif p["func"] == 0:
        desc = "SELECT_CONFIGURATION"
    elif p["func"] == 9:
        desc = f"SELECT_INTERFACE/其他 status=0x{p['status']:08x}"
    print(f"包{i}: irp={p['irp']:#x} func={p['func']} info=0x{p['info']:02x} ep=0x{p['ep']:02x} xfer={p['xfer']} orig={orig_len}")
    print(f"      {desc}")
