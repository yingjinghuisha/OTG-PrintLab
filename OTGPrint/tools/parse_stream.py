#!/usr/bin/env python3
"""PCL3GUI流式解析器 - 完整解码打印流结构"""
import struct, re, sys

with open("/Users/apple/Desktop/项目/OTGPrint/reference_huipu_partial.bin", "rb") as f:
    data = f.read()

# 跳过前导零
pos = next(i for i, b in enumerate(data) if b != 0)

commands = []  # (offset, name, params, data_or_None)
raster_strips = []  # (offset, size, bytes)

def read_pcl(pos):
    """解析从pos开始的PCL命令流"""
    n = len(data)
    events = []
    strips = []
    while pos < n:
        b = data[pos]
        if b == 0x1b:
            # 转义命令
            m = re.match(rb"\x1b[%\*&bplu][^\x1b]*?[A-Za-z]", data[pos:pos+64])
            if not m:
                # 可能是数据里碰到的1b，跳过
                pos += 1
                continue
            cmd = m.group(0)
            # 二进制数据命令 (W结尾带数据)
            if cmd.endswith(b"W") or cmd.endswith(b"w"):
                mm = re.match(rb"\x1b([\*&bpl])([a-z])(-?\d+)([A-Za-z])", cmd)
                if mm and mm.group(4) in (b"W",):
                    size = int(mm.group(3))
                    payload = data[m.end():m.end()+size]
                    events.append((pos, cmd.decode("ascii"), payload))
                    strips.append((pos, size, payload))
                    pos = m.end() + size
                    continue
            events.append((pos, cmd.decode("ascii", errors="replace"), None))
            pos += len(cmd)
        elif b in (0x0a, 0x0d, 0x20, 0x09):
            pos += 1
        elif 0x20 <= b < 0x7f:
            # 可打印文本（PJL等）直到换行
            e = data.find(b"\n", pos)
            if e == -1:
                e = min(pos + 80, n)
            events.append((pos, "TEXT", data[pos:e]))
            pos = e + 1
        else:
            pos += 1
    return events, strips

events, strips = read_pcl(pos)

print("===== 命令流（前60条带数据命令） =====")
for off, cmd, payload in events[:80]:
    if payload is not None:
        print(f"@{off}: {cmd} -> {len(payload)}字节: {payload[:24].hex(' ')}")
    else:
        print(f"@{off}: {cmd}")

print(f"\n总命令数: {len(events)}, 光栅条数: {len(strips)}")

# 光栅条尺寸序列
sizes = [s[1] for s in strips]
print(f"\n前40条光栅尺寸: {sizes[:40]}")
print(f"光栅尺寸范围: {min(sizes)} ~ {max(sizes)}, 总计{sum(sizes)}字节")

# 分析光栅块之间的命令
print("\n===== 光栅块间命令序列（第1-15条周围） =====")
strips_off = {s[0] for s in strips}
idx = 0
for off, cmd, payload in events:
    if off in strips_off:
        idx += 1
        if idx <= 15:
            print(f"[STRIP {idx}] @{off}: {cmd} ({payload is not None and len(payload) or 0}字节)")
    elif idx <= 14:
        print(f"           @{off}: {cmd}")

# 检查是否每条strip后自动换行或有Y偏移命令
# 统计所有 *b?Y 命令
print("\n===== bY命令 =====")
for off, cmd, payload in events:
    if re.match(rb"\x1b\*b-?\d+Y", cmd.encode()) or cmd.startswith("\x1b*b") and cmd.endswith("Y"):
        print(f"@{off}: {cmd}")

# 第2条光栅数据的hex，对比格式
print("\n===== 第2条光栅(177字节) =====")
if len(strips) > 1:
    print(strips[1][2].hex(" "))
print("\n===== 第3条光栅(0字节?) =====")
if len(strips) > 2:
    print(f"size={strips[2][1]}")
print("\n===== 第4条光栅 =====")
if len(strips) > 3:
    print(strips[3][2][:100].hex(" "))
