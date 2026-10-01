#!/usr/bin/env python3
"""PCL3GUI流解析 - 快速版：全局正则扫描b命令"""
import re

with open("/Users/apple/Desktop/项目/OTGPrint/reference_huipu_partial.bin", "rb") as f:
    data = f.read()

# 找所有 \x1b*b<digits><W/V/Y/M> 命令
pat = re.compile(rb"\x1b\*b(\d+)([WVYM])")
matches = list(pat.finditer(data))
print(f"b命令总数: {len(matches)}")

from collections import Counter
terms = Counter(m.group(2) for m in matches)
print(f"终止符统计: {terms}")

# 重建命令序列(检查V/W交替)
seq = [(int(m.group(1)), m.group(2).decode(), m.start()) for m in matches]
print(f"\n前50个: {seq[:50]}")

# V/W模式
pattern = "".join(s[1] for s in seq)
print(f"\nV/W序列(前200): {pattern[:200]}")
print(f"V/W序列(后200): {pattern[-200:]}")

# 验证数据边界: 第i个W命令的payload结束位置应等于第i+1个命令的开始
print("\n===== 边界验证(前20个) =====")
for i in range(min(20, len(seq))):
    size, term, start = seq[i]
    m = matches[i]
    payload_end = m.end() + size
    next_start = matches[i+1].start() if i + 1 < len(matches) else len(data)
    gap = next_start - payload_end
    if i < 20:
        print(f"[{i}] {term}{size} @{start}: payload_end={payload_end}, next_start={next_start}, gap={gap}")
        if 0 < gap < 60:
            print(f"     gap内容: {data[payload_end:next_start].hex(' ')}")

# 检查所有非b命令的转义序列
other_cmds = []
for m in re.finditer(rb"\x1b(?!\*b\d+[WVYM])[\x20-\x7e]{0,20}[A-Za-z]", data):
    if b"\x1b*b" in m.group(0):
        continue
    other_cmds.append((m.start(), m.group(0)))
print(f"\n其他转义命令总数: {len(other_cmds)}")
seen = {}
for off, cmd in other_cmds:
    seen[cmd] = seen.get(cmd, 0) + 1
for cmd, cnt in sorted(seen.items(), key=lambda x: -x[1])[:30]:
    print(f"  {cnt}x {cmd}")

# 统计尺寸
sizes = [s[0] for s in seq if s[1] == "W"]
print(f"\nW块数量: {len(sizes)}, 尺寸范围: {min(sizes)}~{max(sizes)}, 总计: {sum(sizes)}字节")
if terms.get("V"):
    vsizes = [s[0] for s in seq if s[1] == "V"]
    print(f"V块数量: {len(vsizes)}, 尺寸范围: {min(vsizes)}~{max(vsizes)}, 总计: {sum(vsizes)}字节")

# 检查行数: 每行应该是 V+W 一对(或者只有W)
vw_pairs = 0
w_only = 0
i = 0
rows = 0
while i < len(seq):
    if i + 1 < len(seq) and seq[i][1] == "V" and seq[i+1][1] == "W":
        vw_pairs += 1
        i += 2
    elif seq[i][1] == "W":
        w_only += 1
        i += 1
    else:
        i += 1
print(f"\nV+W对: {vw_pairs}, 单独W: {w_only}")
