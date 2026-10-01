#!/usr/bin/env python3
"""分析PCL3GUI数据流结构"""
import struct, re

with open("/Users/apple/Desktop/项目/OTGPrint/reference_huipu_partial.bin", "rb") as f:
    data = f.read()

print(f"总长度: {len(data)}")

# 1. 确认前导零
first_nz = next(i for i, b in enumerate(data) if b != 0)
print(f"前导零字节数: {first_nz}")

# 2. UEL + PJL头部区域
print("\n===== UEL/PJL区域 (10990-11050) =====")
print(data[10990:11060].hex(" "))
print("".join(chr(b) if 32 <= b < 127 else "." for b in data[10990:11060]))

# 3. 找 ENTER LANGUAGE=PCL3GUI 之后的内容
idx = data.find(b"ENTER LANGUAGE=PCL3GUI")
print(f"\nENTER LANGUAGE=PCL3GUI @ {idx}")
pcl_start = data.find(b"\n", idx) + 1
# 检查是否还有后续PJL行（SET USERNAME等）
print(f"PCL数据起始: {pcl_start}")
print("\n===== PCL3GUI起始区域 =====")
chunk = data[pcl_start:pcl_start+600]
print(chunk.hex(" "))
print()
print("".join(chr(b) if 32 <= b < 127 else "." for b in chunk))

# 4. 解析所有PCL转义命令（在pcl_start之后的前8KB）
print("\n===== PCL转义命令序列 (前8KB) =====")
region = data[pcl_start:pcl_start+8192]
# PCL命令: ESC + 参数(数字/符号) + 终结字母(小写) 或 ESC&b十六进制块等
i = 0
count = 0
while i < len(region) and count < 100:
    b = region[i]
    if b == 0x1b:
        # 提取完整命令
        j = i + 1
        cmd = bytearray(b"\x1b")
        while j < len(region):
            c = region[j]
            cmd.append(c)
            if 0x61 <= c <= 0x7a or 0x41 <= c <= 0x5a:  # 字母终结符
                break
            j += 1
        s = bytes(cmd)
        if len(s) <= 40:
            print(f"@{pcl_start+i}: {s.decode('ascii', errors='replace')}")
        else:
            print(f"@{pcl_start+i}: {s[:40].decode('ascii', errors='replace')}... ({len(s)}字节)")
        i = j + 1
        count += 1
    else:
        i += 1

# 5. 统计整个流中的压缩行标记 (PCL3GUI的 \x1b*b...W / \x1b*b...V 等)
print("\n===== 光栅数据命令统计 =====")
for pat, desc in [
    (rb"\x1b\*b(\d+)W", "Transfer Raster Data (bW)"),
    (rb"\x1b\*b(\d+)V", "bV block"),
    (rb"\x1b\*b(\d+)M", "Set Compression"),
    (rb"\x1b\*r(\d+)A", "Start Raster"),
    (rb"\x1b\*r(\d+)B", "End Raster"),
    (rb"\x1b\*r(\d+)S", "rS"),
    (rb"\x1b\*t(\d+)R", "Set Resolution"),
    (rb"\x1b\*r(\d+)P", "rP"),
    (rb"\x1b\*b(\d+)Y", "bY offset"),
    (rb"\x1b&l(\d+)A", "Page Size"),
    (rb"\x1b&l(\d+)H", "Paper Source"),
    (rb"\x1b&l(\d+)O", "Orientation"),
    (rb"\x1b&l(\d+)d", "ld"),
    (rb"\x1b\*p(\d+)X", "Cursor X"),
    (rb"\x1b\*p(\d+)Y", "Cursor Y"),
    (rb"\x1b\*c(\d+)A", "cA"),
    (rb"\x1b\*c(\d+)B", "cB"),
    (rb"\x1b\*v(\d+)", "*v"),
    (rb"\x1b\*g(\d+)W", "*gW"),
    (rb"\x1b\*i(\d+)", "*i"),
    (rb"\x1b%(\d+)A", "%A"),
    (rb"\x1b&b(\d+)W", "&bW"),
]:
    matches = re.findall(pat, data)
    if matches:
        uniq = list(dict.fromkeys(matches[:20]))
        print(f"{desc}: {len(matches)}次, 值样本: {uniq[:15]}")
