#!/usr/bin/env python3
"""全面分析参考PCL3-GUI流的命令序列: 头部/设置/光栅命令/页结束"""
import re
import sys

SRC = "/Users/apple/Desktop/项目/OTGPrint/reference_huipu_partial.bin"

with open(SRC, "rb") as f:
    data = f.read()

print(f"流总长度: {len(data)}")

# 找到PJL头部开始位置
pjl_start = data.find(b"\x00@PJL")
if pjl_start < 0:
    pjl_start = data.find(b"@PJL")
print(f"@PJL起始: {pjl_start}")

# 逐命令解析: \x1b 后跟参数和终结符
# PCL命令: ESC * X ... X (大小写字母结尾)
cmd_pat = re.compile(rb"\x1b[%*&(=E]\??[0-9A-Za-z+\-. ]*[A-Za-z]")
pos = pjl_start
cmds = []
raster_count = 0
while pos < len(data):
    # 检查PJL行
    if data[pos:pos+5] == b"@PJL " or data[pos:pos+4] == b"@PJL":
        eol = data.find(b"\n", pos)
        if eol < 0:
            eol = len(data)
        line = data[pos:eol].decode("ascii", errors="replace").strip()
        cmds.append((pos, "PJL", line))
        pos = eol + 1
        continue
    m = cmd_pat.match(data, pos)
    if not m:
        # 跳过一个字节
        pos += 1
        continue
    s = m.group(0)
    cmds.append((pos, "PCL", s.decode("latin1")))
    pos = m.end()

# 输出所有非光栅W块的命令(压缩显示)
print("\n===== 命令序列 (前200条非W数据) =====")
shown = 0
for off, typ, s in cmds:
    if shown >= 200:
        break
    if len(s) > 60:
        s = s[:60] + "..."
    print(f"@{off:8d} [{typ}] {s!r}")
    shown += 1

# 统计 *b 命令
print("\n===== *b 命令统计 =====")
b_pat = re.compile(rb"\x1b\*b(\d+)([WVMY])")
# 顺序扫描避免数据内假阳性
pos = pjl_start
b_cmds = []
while pos < len(data):
    m = b_pat.search(data, pos)
    if not m:
        break
    size = int(m.group(1))
    term = m.group(2).decode()
    b_cmds.append((m.start(), size, term))
    if term == "W":
        pos = m.end() + size
    else:
        pos = m.end()

from collections import Counter
terms = Counter(t for _, _, t in b_cmds)
print(f"*b命令总数: {len(b_cmds)}, 终止符: {dict(terms)}")
# 显示所有非W的*b命令
non_w = [(o, s, t) for o, s, t in b_cmds if t != "W"]
print(f"非W的*b命令 ({len(non_w)}个):")
for o, s, t in non_w[:50]:
    print(f"  @{o}: *b{s}{t}")

# W块大小分布
w_sizes = [s for _, s, t in b_cmds if t == "W"]
if w_sizes:
    print(f"W块: {len(w_sizes)}个, 最小{min(w_sizes)}, 最大{max(w_sizes)}, 总{sum(w_sizes)}字节")

# Y命令的位置和值
y_cmds = [(o, s) for o, s, t in b_cmds if t == "Y"]
print(f"Y命令位置(前20): {y_cmds[:20]}")

# 通用命令统计(不含b)
print("\n===== PCL命令统计 =====")
all_pat = re.compile(rb"\x1b\*([a-z])([0-9]*)([A-Z])")
pos = pjl_start
gen_cmds = Counter()
while pos < len(data):
    m = b_pat.search(data, pos)
    # 逐个通用命令扫描
    gm = all_pat.match(data, pos)
    if gm:
        key = f"*{gm.group(1).decode()}{gm.group(3).decode()}"
        val = gm.group(2).decode()
        gen_cmds[f"{key}({val})"] += 1
        pos = gm.end()
        continue
    if m and m.start() == pos:
        size = int(m.group(1))
        if m.group(2) == b"W":
            pos = m.end() + size
        else:
            pos = m.end()
        continue
    pos += 1

for k, v in sorted(gen_cmds.items()):
    print(f"  {k}: {v}次")
