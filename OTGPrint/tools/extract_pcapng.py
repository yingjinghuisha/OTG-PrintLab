#!/usr/bin/env python3
"""
从 USBPcap 抓包文件(pcapng)中提取发往打印机的 BULK-OUT 数据流。
纯Python实现，不依赖 tshark / scapy / pyshark。
用法: python3 extract_pcapng.py <input.pcapng> <output.bin>
"""
import struct
import sys

SHB = 0x0A0D0D0A
IDB = 0x00000001
EPB = 0x00000006

def parse_pcapng(path):
    """逐块解析pcapng，返回EPB列表 (interface_id, packet_data)"""
    packets = []
    with open(path, "rb") as f:
        data = f.read()
    pos = 0
    total = len(data)
    while pos + 12 <= total:
        block_type, block_len = struct.unpack_from("<II", data, pos)
        if block_len < 12 or pos + block_len > total:
            break
        if block_type == EPB:
            if_id, ts_h, ts_l, cap_len, orig_len = struct.unpack_from("<IIIII", data, pos + 8)
            pkt = data[pos + 28 : pos + 28 + cap_len]
            packets.append((if_id, pkt))
        pos += block_len
    return packets

def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    src, dst = sys.argv[1], sys.argv[2]
    packets = parse_pcapng(src)
    print(f"共解析 {len(packets)} 个数据包")

    out = bytearray()
    n_bulk_out = 0
    endpoints = {}
    transfers = {}

    for if_id, pkt in packets:
        if len(pkt) < 28:
            continue
        # USBPcap header: headerLen(2) irpId(8) status(4) function(2) info(1)
        #                 bus(2) device(2) endpoint(1) transfer(1) dataLength(4)
        header_len = struct.unpack_from("<H", pkt, 0)[0]
        if header_len < 27:
            continue
        info = pkt[17]
        bus, dev = struct.unpack_from("<HH", pkt, 18)
        endpoint = pkt[22]
        transfer = pkt[23]
        data_len = struct.unpack_from("<I", pkt, 24)[0]

        transfers[transfer] = transfers.get(transfer, 0) + 1

        # transfer: 0=ISO 1=INTERRUPT 2=CONTROL 3=BULK
        if transfer != 3:
            continue
        # info bit0=1: host->device (OUT方向)
        host_to_dev = bool(info & 0x01)
        ep_addr = endpoint & 0x7F
        ep_dir_out = (endpoint & 0x80) == 0
        endpoints[(ep_addr, "OUT" if ep_dir_out else "IN")] = endpoints.get((ep_addr, "OUT" if ep_dir_out else "IN"), 0) + 1

        if host_to_dev and ep_dir_out and data_len > 0:
            payload = pkt[header_len : header_len + data_len]
            out += payload[:data_len]
            n_bulk_out += 1

    with open(dst, "wb") as f:
        f.write(out)

    print(f"transfer类型统计: {transfers}")
    print(f"端点统计: {endpoints}")
    print(f"BULK-OUT 包数量: {n_bulk_out}")
    print(f"输出总字节数: {len(out)}")
    print(f"已写入: {dst}")

    # 打印前256字节用于协议分析
    print("\n===== 前256字节 (hex) =====")
    print(out[:256].hex(" "))
    printable = "".join(chr(b) if 32 <= b < 127 else "." for b in out[:512])
    print("\n===== 前512字节 (可读字符) =====")
    print(printable)

if __name__ == "__main__":
    main()
