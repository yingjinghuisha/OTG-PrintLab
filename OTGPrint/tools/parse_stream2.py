#!/usr/bin/env python3
"""PCL3GUI流精确解析器 - 状态机方式"""
import struct, re

with open("/Users/apple/Desktop/项目/OTGPrint/reference_huipu_partial.bin", "rb") as f:
    data = f.read()

pos = next(i for i, b in enumerate(data) if b != 0)

# 先跳过PJL区域
idx = data.find(b"ENTER LANGUAGE=PCL3GUI")
pos = data.find(b"\n", idx) + 1

# 解析PJL后的命令
events = []
n = len(data)
while pos < n:
    if data[pos] == 0x1b:
        # 尝试匹配 *b#W / *b#V / 其他
        m = re.match(rb"\x1b\*b(\d+)([WVY])", data[pos:pos+16])
        if m:
            size = int(m.group(1))
            term = m.group(2)
            if term in (b"W", b"V"):
                payload = data[m.end():m.end()+size]
                events.append(("RASTER", term.decode(), size, payload, pos))
                pos = m.end() + size
            else:
                events.append(("CMD", f"*b{m.group(1).decode()}Y", None, None, pos))
                pos = m.end()
            continue
        m = re.match(rb"\x1b[\x20-\x7e]{1,30}[A-Za-z]", data[pos:pos+32])
        if m:
            events.append(("CMD", m.group(0).decode("ascii", errors="replace"), None, None, pos))
            pos += len(m.group(0))
            continue
        pos += 1
    elif data[pos] in (0x0a, 0x0d):
        pos += 1
    else:
        # 文本
        e = pos
        while e < n and data[e] not in (0x0a, 0x1b):
            e += 1
        events.append(("TEXT", data[pos:e].decode("ascii", errors="replace"), None, None, pos))
        pos = e

# 统计
vw = {}
for ev in events:
    if ev[0] == "RASTER":
        key = ev[1]
        vw[key] = vw.get(key, 0) + 1

print(f"光栅块统计: {vw}")
print(f"\n===== 前30个光栅块 =====")
raster_count = 0
for i, ev in enumerate(events):
    if ev[0] == "RASTER":
        raster_count += 1
        if raster_count <= 30:
            print(f"[{raster_count}] {ev[1]} size={ev[2]} @{ev[4]}: {ev[3][:20].hex(' ')}")
    elif ev[0] == "CMD" and raster_count <= 30:
        print(f"      CMD: {ev[1]} @{ev[4]}")

# 检查V/W交替模式
pattern = "".join(ev[1] if ev[0] == "RASTER" else "" for ev in events[:200])
print(f"\n前200个光栅块的V/W模式: {pattern[:100]}")

# 尺寸序列
sizes = [(ev[1], ev[2]) for ev in events if ev[0] == "RASTER"]
print(f"\n前40个尺寸: {sizes[:40]}")

# 检查所有非光栅命令
print(f"\n===== 所有非光栅命令 =====")
for ev in events:
    if ev[0] == "CMD":
        print(f"@{ev[4]}: {ev[1]}")

# 保存第一个和最后一个光栅块用于深度分析
rasters = [ev for ev in events if ev[0] == "RASTER"]
with open("/tmp/strip_analysis.txt", "w") as f:
    for i, r in enumerate(rasters[:50]):
        f.write(f"strip {i}: {r[1]} {r[2]}B: {r[3].hex()}\n")
print("\nstrip数据已保存到 /tmp/strip_analysis.txt")
