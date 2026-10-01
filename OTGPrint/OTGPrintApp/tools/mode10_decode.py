#!/usr/bin/env python3
"""
Mode10 (Bert Compression) 解码器 - 验证对Windows驱动PCL3GUI流的理解
解码 reference_huipu_partial.bin 中的光栅条并渲染成PPM图片
"""
import re
import struct

SRC = "/Users/apple/Desktop/项目/OTGPrint/reference_huipu_partial.bin"
OUT = "/Users/apple/Desktop/项目/OTGPrint/decoded_preview.ppm"

WIDTH = 4891  # *r4891S
ROW_OFFSET = 82  # *b82Y


def read_vli(data, pos):
    """VLI: 累加字节; 255继续; <255结束"""
    total = 0
    while True:
        b = data[pos]
        pos += 1
        total += b
        if b != 255:
            break
    return total, pos


def read_pixel(block, pos, seed, cur):
    """从block读取一个编码像素(短delta或全量), 返回(像素, 新pos)"""
    b0 = block[pos]
    if b0 & 0x80:
        b1 = block[pos + 1]
        pos += 2
        val = (b0 << 8) | b1
        dr = ((val >> 10) & 0x1F)
        dg = ((val >> 5) & 0x1F)
        db_raw = val & 0x1F
        db = db_raw << 1
        if dr >= 16:
            dr -= 32
        if dg >= 16:
            dg -= 32
        if db_raw >= 16:
            db -= 64
        sp = seed[cur]
        p = (max(0, min(255, sp[0] + dr)),
             max(0, min(255, sp[1] + dg)),
             max(0, min(255, sp[2] + db)))
    else:
        b1 = block[pos + 1]
        b2 = block[pos + 2]
        pos += 3
        v = (b0 << 16) | (b1 << 8) | b2
        v <<= 1
        p = ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)
    return p, pos


def decode_row(block, seed):
    """解码一行Mode10数据 -> 当前行像素列表[(r,g,b)...]"""
    row = [(seed[i][0], seed[i][1], seed[i][2]) for i in range(WIDTH)]
    cur = 0
    pos = 0
    cached = (255, 255, 254)  # kWhite
    n = len(block)

    while pos < n and cur < WIDTH:
        cmd = block[pos]
        pos += 1
        cmd_type = cmd & 0x80
        source = cmd & 0x60
        seed_field = (cmd >> 3) & 0x03
        count_field = cmd & 0x07

        # seed row复制
        if seed_field == 3:
            extra, pos = read_vli(block, pos)
            seed_copy = 3 + extra
        else:
            seed_copy = seed_field
        for i in range(seed_copy):
            if cur < WIDTH:
                row[cur] = seed[cur]
                cur += 1

        if cmd_type == 0:  # LITERAL
            count = count_field + 1
            first_from_source = source != 0
            run_start = cur  # 本run第一个像素位置(用于cached更新)
            if first_from_source:
                # 第一个像素来自替代源
                if source == 0x20:  # West
                    p = row[cur - 1] if cur > 0 else (255, 255, 254)
                elif source == 0x40:  # NorthEast (seed[cur+1])
                    p = seed[cur + 1] if cur + 1 < WIDTH else (255, 255, 254)
                else:  # Cached
                    p = cached
                row[cur] = p
                cur += 1
                to_decode = count - 1
            else:
                to_decode = count

            if count_field == 7:
                # spill扩展: 先解码首块
                first_block = 8 - (1 if first_from_source else 0)
                for _ in range(min(first_block, to_decode)):
                    to_decode -= 1
                    p, pos = read_pixel(block, pos, seed, cur)
                    row[cur] = p
                    cur += 1
                # spill循环: spill字节是单字节(255=继续, <255=结束), 非求和VLI
                while True:
                    s = block[pos]
                    pos += 1
                    if s == 0:
                        break
                    for _ in range(s):
                        p, pos = read_pixel(block, pos, seed, cur)
                        row[cur] = p
                        cur += 1
                    if s < 255:
                        break
            else:
                for _ in range(to_decode):
                    p, pos = read_pixel(block, pos, seed, cur)
                    row[cur] = p
                    cur += 1
            if source == 0x00:
                # 新颜色literal: 缓存 = 本run第一个像素
                if 0 <= run_start < WIDTH:
                    cached = row[run_start]
        else:  # RLE
            count = count_field + 2
            if source == 0x00:  # 新像素编码
                p, pos = read_pixel(block, pos, seed, cur)
                cached = p
            elif source == 0x20:  # West
                p = row[cur - 1] if cur > 0 else (255, 255, 254)
            elif source == 0x40:  # NE
                p = seed[cur + 1] if cur + 1 < WIDTH else (255, 255, 254)
            else:  # cached
                p = cached
            if count_field == 7:
                extra, pos = read_vli(block, pos)
                count = 9 + extra
            for _ in range(count):
                if cur < WIDTH:
                    row[cur] = p
                    cur += 1

    return row, pos


def main():
    with open(SRC, "rb") as f:
        data = f.read()

    # 顺序解析: 跳过W块内容避免数据内部假阳性 \x1b*b#W
    strips = []
    pos = 0
    n = len(data)
    cmd_pat = re.compile(rb"\x1b\*b(\d+)([WVYM])")
    other = 0
    while pos < n:
        m = cmd_pat.search(data, pos)
        if not m:
            break
        size = int(m.group(1))
        term = m.group(2)
        if term == b"W":
            strips.append(data[m.end():m.end() + size])
            pos = m.end() + size
        else:
            other += 1
            pos = m.end()
    print(f"W块数量: {len(strips)}, 其他b命令: {other}")

    # 解码
    seed = [(255, 255, 254)] * WIDTH  # 初始seed = kWhite
    rows = []
    errors = 0
    for i, strip in enumerate(strips):
        try:
            row, consumed = decode_row(strip, seed)
            if consumed != len(strip):
                if errors < 5:
                    print(f"警告: strip {i} 消耗{consumed}/{len(strip)}字节")
                errors += 1
            rows.append(row)
            seed = row
        except Exception as e:
            print(f"strip {i} 解码异常: {e}")
            rows.append([(255, 255, 254)] * WIDTH)
            errors += 1
            if errors > 10:
                break

    print(f"解码完成: {len(rows)}行, 异常{errors}个")

    # 渲染PPM (缩小到1/8)
    scale = 8
    w = WIDTH // scale
    h = len(rows) // scale
    with open(OUT, "wb") as f:
        f.write(b"P6\n%d %d\n255\n" % (w, h))
        for y in range(h):
            line = bytearray()
            for x in range(w):
                p = rows[y * scale][x * scale]
                line += bytes(p)
            f.write(line)
    print(f"已输出: {OUT} ({w}x{h})")


if __name__ == "__main__":
    main()
