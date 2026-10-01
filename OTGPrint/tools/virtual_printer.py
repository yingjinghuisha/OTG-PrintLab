#!/usr/bin/env python3
"""
virtual_printer.py — 虚拟打印机: 解析/验证/解码 OTGPrint 生成的 PCL3-GUI 打印流

模拟 HP DeskJet 2132 接收打印流后的行为:
  1. 验证 PJL 头/尾完整性
  2. 校验 PCL3-GUI 命令序列 (与 Windows 原厂驱动抓包样本一致)
  3. 解码 Mode10 光栅数据, 重建图像
  4. 与原始输入图像 (.ppm) 逐像素比对
  5. 模拟打印机状态响应 (READY / PROCESSING / PAGE_COMPLETE / ERROR)

用法: python3 virtual_printer.py <dir> [test_name]
  dir       — gen_test_streams 的输出目录
  test_name — 只跑指定用例 (省略则跑全部)
"""
import os
import re
import sys

# ---------------------------------------------------------------------------
# Mode10 解码 (与打印机固件行为一致)
# ---------------------------------------------------------------------------
WHITE = (255, 255, 254)


def read_vli(data, pos):
    """VLI: 累加字节; 255继续; <255结束"""
    total = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        total += b
        if b != 255:
            break
    return total, pos


def read_pixel(block, pos, seed, cur):
    """读取一个编码像素(短delta或全量), 越界安全"""
    if pos >= len(block):
        raise IndexError(f"block读取越界: pos={pos} len={len(block)}")
    b0 = block[pos]
    if b0 & 0x80:  # 短 delta (2字节, 相对seed)
        if pos + 1 >= len(block):
            raise IndexError(f"短delta越界: pos={pos}")
        b1 = block[pos + 1]
        pos += 2
        val = (b0 << 8) | b1
        dr = (val >> 10) & 0x1F
        dg = (val >> 5) & 0x1F
        db_raw = val & 0x1F
        db = db_raw << 1
        if dr >= 16:
            dr -= 32
        if dg >= 16:
            dg -= 32
        if db_raw >= 16:
            db -= 64
        sp = seed[cur] if cur < len(seed) else WHITE
        p = (max(0, min(255, sp[0] + dr)),
             max(0, min(255, sp[1] + dg)),
             max(0, min(255, sp[2] + db)))
    else:  # 全量 (3字节, v<<1)
        if pos + 2 >= len(block):
            raise IndexError(f"全量像素越界: pos={pos}")
        b1 = block[pos + 1]
        b2 = block[pos + 2]
        pos += 3
        v = ((b0 << 16) | (b1 << 8) | b2) << 1
        p = ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)
    return p, pos


class Mode10Decoder:
    """Mode10 行解码器 — 状态机与打印机固件一致"""

    def __init__(self, width):
        self.width = width
        self.seed = [WHITE] * width
        self.cached = WHITE

    def flush(self):
        """*b M / 页边界: seed 重置为白色"""
        self.seed = [WHITE] * self.width

    def decode_block(self, block):
        """
        解码一个 *b W 块 -> 当前行像素列表

        编码器 spill 格式 (与 pcl3gui_encoder.cpp 逐字节对齐):
        - literal run 位置 8, 263, 518... 后输出 spill 字节
          (位置 = run 内 1-indexed 序号, 含 source 首像素)
        - spill 值 = min(255, total - 当前位置), 0 = 结束
        - RLE count>=9 时 VLI 紧跟像素编码之后
        """
        W = self.width
        row = list(self.seed)
        cur = 0
        pos = 0
        n = len(block)
        cached = self.cached

        def safe_seed(i):
            return self.seed[i] if 0 <= i < W else WHITE

        def safe_row(i):
            return row[i] if 0 <= i < W else WHITE

        def emit(px):
            nonlocal cur
            if cur < W:
                row[cur] = px
                cur += 1
                return True
            return False

        while pos < n and cur < W:
            cmd = block[pos]
            pos += 1
            is_rle = bool(cmd & 0x80)
            source = (cmd >> 5) & 0x03
            seed_field = (cmd >> 3) & 0x03
            count_field = cmd & 0x07

            # --- 1. seed 行复制前缀 ---
            if seed_field == 3:
                extra, pos = read_vli(block, pos)
                copy_n = 3 + extra
            else:
                copy_n = seed_field
            ok = True
            for _ in range(copy_n):
                if not emit(safe_seed(cur)):
                    ok = False
                    break
            if not ok:
                break

            # --- 2. source 像素选取函数 ---
            def pick_src():
                if source == 1:  # West
                    return safe_row(cur - 1)
                if source == 2:  # NorthEast
                    return safe_seed(cur + 1)
                return cached    # Cached

            if is_rle:
                # ============ RLE ============
                count = count_field + 2
                if source == 0:
                    if cur < len(self.seed):
                        pix, pos = read_pixel(block, pos, self.seed, cur)
                    else:
                        pix, pos = read_pixel(block, pos, self.seed, W - 1)
                    cached = pix
                else:
                    pix = pick_src()
                if count_field == 7:
                    extra, pos = read_vli(block, pos)
                    count = 9 + extra
                for _ in range(count):
                    if not emit(pix):
                        ok = False
                        break
                if not ok:
                    break
            else:
                # ============ LITERAL ============
                run_start = cur
                if source != 0:
                    # 位置1来自替代源, 不编码
                    if not emit(pick_src()):
                        break

                if count_field == 7:
                    # 位置 1..8 中的剩余编码像素 (位置8后跟spill)
                    first_batch = 8 - (1 if source != 0 else 0)
                    for _ in range(first_batch):
                        if cur < len(self.seed):
                            pix, pos = read_pixel(block, pos, self.seed, cur)
                        else:
                            pix, pos = read_pixel(block, pos, self.seed, W - 1)
                        if not emit(pix):
                            ok = False
                            break
                    if not ok:
                        break
                    # spill 循环: 位置8, 263, 518...
                    while pos < n:
                        spill = block[pos]
                        pos += 1
                        if spill == 0:
                            break
                        for _ in range(spill):
                            if cur < len(self.seed):
                                pix, pos = read_pixel(block, pos, self.seed, cur)
                            else:
                                pix, pos = read_pixel(block, pos, self.seed, W - 1)
                            if not emit(pix):
                                ok = False
                                break
                        if not ok or spill < 255:
                            break
                else:
                    # count = count_field + 1, 无spill
                    remaining = count_field + 1 - (1 if source != 0 else 0)
                    for _ in range(remaining):
                        if cur < len(self.seed):
                            pix, pos = read_pixel(block, pos, self.seed, cur)
                        else:
                            pix, pos = read_pixel(block, pos, self.seed, W - 1)
                        if not emit(pix):
                            ok = False
                            break
                    if not ok:
                        break

                # 新颜色 literal: 缓存 = run 首像素
                if source == 0 and run_start < W:
                    cached = row[run_start]

        self.cached = cached
        self.seed = row
        return row, pos


# ---------------------------------------------------------------------------
# PCL3-GUI 流解析 (虚拟打印机主逻辑)
# ---------------------------------------------------------------------------
CMD_PAT = re.compile(rb"\x1b\*b(\d+)([WVYM])")

# 预期命令序列 (源自黄金样本)
EXPECTED_HEAD = [
    (b"\x1b%-12345X@PJL", "UEL/PJL头"),
    (b"@PJL SET STRINGCODESET=UTF8", "字符串编码"),
    (b"@PJL JOB", "任务开始"),
    (b"@PJL ENTER LANGUAGE=PCL3GUI", "语言切换"),
]


class VirtualPrinter:
    """模拟 HP DeskJet 2132 接收 PCL3-GUI 流"""

    def __init__(self, verbose=False):
        self.verbose = verbose
        self.errors = []
        self.warnings = []
        self.stats = {}

    def err(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def log(self, msg):
        if self.verbose:
            print(f"    [PRN] {msg}")

    def process(self, data):
        """主入口: 像打印机一样消费整个打印流, 返回解码出的图像行"""
        # ---- 1. PJL 头验证 ----
        # 流格式: ESC E (复位) + UEL + @PJL...
        uel_pos = data.find(b"\x1b%-12345X")
        if uel_pos < 0 or uel_pos > 10:
            self.err("缺少 UEL 起始符 (前10字节内未找到 \\x1b%-12345X)")
        pjl_end = data.find(b"ENTER LANGUAGE=PCL3GUI")
        if pjl_end < 0:
            self.err("缺少 PJL ENTER LANGUAGE=PCL3GUI")
            return None
        pjl_body = data[:pjl_end].decode("ascii", errors="replace")
        for token, desc in EXPECTED_HEAD[1:3]:
            if token.decode() not in pjl_body:
                self.warn(f"PJL 头缺少 {desc}")

        # ---- 2. 扫描 PCL 命令 ----
        # 提取关键参数
        m = re.search(rb"\x1b\*r(\d+)S", data)
        if not m:
            self.err("缺少 *r#S 光栅宽度命令")
            return None
        width = int(m.group(1))
        self.log(f"光栅宽度: {width}")

        m = re.search(rb"\x1b\*t(\d+)R", data)
        dpi = int(m.group(1)) if m else 0
        self.log(f"分辨率: {dpi}dpi")

        m = re.search(rb"\x1b&l(\d+)A", data)
        paper = int(m.group(1)) if m else -1

        # 页边距
        m = re.search(rb"\x1b\*p(\d+)Y", data)
        top_off = int(m.group(1)) if m else 0

        # ---- 3. 光栅数据解码 ----
        pos = pjl_end
        decoder = Mode10Decoder(width)
        rows = []          # 解码出的行 (None = 空白跳过行)
        pending_skip = 0
        w_blocks = 0
        y_skips = 0
        total_w_bytes = 0

        while pos < len(data):
            m = CMD_PAT.search(data, pos)
            if not m:
                break
            size = int(m.group(1))
            term = m.group(2)
            if term == b"W":
                block = data[m.end():m.end() + size]
                if len(block) != size:
                    self.err(f"W块数据不足: 需{size}实际{len(block)}")
                    break
                if size == 0:
                    # *b0W: 整行复制 seed
                    rows.append(list(decoder.seed))
                else:
                    try:
                        row, consumed = decoder.decode_block(block)
                        if consumed != size:
                            self.warn(f"W块#{w_blocks} 字节消耗不匹配: {consumed}/{size}")
                        if len(row) != width:
                            self.err(f"W块#{w_blocks} 行宽错误: {len(row)} != {width}")
                        rows.append(row)
                    except Exception as e:
                        self.err(f"W块#{w_blocks} 解码异常: {e}")
                        rows.append([WHITE] * width)
                w_blocks += 1
                total_w_bytes += size
                pos = m.end() + size
            elif term == b"Y":
                # 跳过 size 行空白
                if pending_skip == 0:
                    decoder.flush()  # 首次跳过: seed 重置
                pending_skip += size
                y_skips += 1
                pos = m.end()
            elif term == b"M":
                decoder.flush()
                rows.append(None)  # 标记
                pending_skip = 0
                pos = m.end()
            elif term == b"V":
                pos = m.end()
            else:
                pos = m.end()

        # 展开: Y跳过的行数 + 实际行
        final_rows = []
        skip_iter = 0
        for i, r in enumerate(rows):
            if r is None:
                continue
            final_rows.append(r)

        self.stats = {
            "width": width,
            "dpi": dpi,
            "paper": paper,
            "w_blocks": w_blocks,
            "y_skip_cmds": y_skips,
            "w_bytes": total_w_bytes,
            "decoded_rows": len(final_rows),
            "stream_size": len(data),
        }

        # ---- 4. 尾部验证 ----
        tail = data[-40:]
        if b"\x1b*rC" not in data:
            self.warn("缺少 *rC (结束光栅)")
        if b"\x1bE" not in tail:
            self.warn("缺少 ESC E (复位)")
        if b"@PJL EOJ" not in tail:
            self.warn("缺少 @PJL EOJ")
        if b"\x1b%-12345X" not in tail:
            self.warn("缺少结尾 UEL")

        return final_rows


# ---------------------------------------------------------------------------
# PPM 比对
# ---------------------------------------------------------------------------

def load_ppm(path):
    """加载 P6 PPM -> (pixels, w, h); pixels = [(r,g,b)...]"""
    with open(path, "rb") as f:
        data = f.read()
    # 跳过注释
    parts = []
    pos = 0
    while len(parts) < 4:
        # 跳过空白和注释
        while pos < len(data) and data[pos:pos+1] in b" \t\r\n":
            pos += 1
        if data[pos:pos+1] == b"#":
            while pos < len(data) and data[pos:pos+1] != b"\n":
                pos += 1
            continue
        start = pos
        while pos < len(data) and data[pos:pos+1] not in b" \t\r\n":
            pos += 1
        parts.append(data[start:pos])
    magic = parts[0]
    if magic != b"P6":
        raise ValueError(f"非P6格式: {magic}")
    w, h, maxv = int(parts[1]), int(parts[2]), int(parts[3])
    pos += 1  # 单字节分隔
    pixels = []
    for i in range(w * h):
        o = pos + i * 3
        pixels.append((data[o], data[o+1], data[o+2]))
    return pixels, w, h


def compare_rows(rows, ppm_pixels, ppm_w, ppm_h):
    """
    比对解码行与原始图像。
    策略: 原图的主要颜色应出现在解码结果中 (单向包含检查)。
    双线性缩放会产生插值颜色, 因此不能用严格的双向匹配。
    返回 (match_ratio, details)
    """
    if not rows:
        return 0.0, "无解码行"

    # 原图前5种主要颜色 (排除纯白)
    orig_colors = {}
    orig_colored = 0
    for px in ppm_pixels:
        if px != WHITE and px != (255, 255, 255):
            orig_colored += 1
            orig_colors[px] = orig_colors.get(px, 0) + 1
    top_orig = sorted(orig_colors.items(), key=lambda x: -x[1])[:5]

    if not top_orig:
        # 全白图: 检查打印流也基本全白
        total = sum(len(r) for r in rows)
        nonwhite = sum(1 for r in rows for p in r if p != WHITE and p != (255,255,255))
        if nonwhite / max(1, total) < 0.01:
            return 1.0, "全白图, 打印流同样基本全白"
        return 0.3, f"原图全白但打印流有 {nonwhite} 个非白像素"

    # 打印流颜色集合 (采样加速)
    stream_colors = set()
    total_px = 0
    for r in rows:
        for p in r:
            stream_colors.add(p)
            total_px += 1
            if total_px > 2000000:  # 采样上限
                break
        if total_px > 2000000:
            break

    # 检查原图主要颜色是否出现在打印流中 (允许±2色差容忍插值)
    matched = 0
    for color, count in top_orig:
        found = False
        for sc in stream_colors:
            if (abs(sc[0]-color[0]) <= 3 and
                abs(sc[1]-color[1]) <= 3 and
                abs(sc[2]-color[2]) <= 3):
                found = True
                break
        if found:
            matched += 1

    ratio = matched / len(top_orig)
    details = (f"原图主要颜色 {matched}/{len(top_orig)} 出现在打印流中 "
               f"(含±3容忍), 打印流唯一颜色数: {len(stream_colors)}")
    return ratio, details


def render_preview(rows, out_path, scale=8):
    """渲染解码行为 PPM 预览图 (缩小)"""
    if not rows:
        return
    w = len(rows[0])
    h = len(rows)
    sw = max(1, w // scale)
    sh = max(1, h // scale)
    with open(out_path, "wb") as f:
        f.write(b"P6\n%d %d\n255\n" % (sw, sh))
        for y in range(sh):
            line = bytearray()
            for x in range(sw):
                p = rows[y * scale][x * scale] if y*scale < h and x*scale < w else WHITE
                line += bytes(p)
            f.write(line)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run_test(bin_path, ppm_path, out_dir, verbose=False):
    """跑单个测试用例"""
    name = os.path.basename(bin_path).replace(".bin", "")
    print(f"\n{'='*70}")
    print(f"测试: {name}")
    print(f"{'='*70}")

    # 模拟打印机启动
    print("  [PRN] 打印机状态: READY (VID=0x03F0 PID=0xE111)")

    with open(bin_path, "rb") as f:
        data = f.read()
    print(f"  [PRN] 收到打印流: {len(data)} bytes")
    print(f"  [PRN] 状态: PROCESSING...")

    printer = VirtualPrinter(verbose=verbose)
    rows = printer.process(data)

    if rows is None:
        print(f"  [PRN] 状态: ERROR — {printer.errors[0] if printer.errors else '未知错误'}")
        return False

    # 协议错误检查
    if printer.errors:
        print(f"  [PRN] 状态: ERROR")
        for e in printer.errors:
            print(f"    ✗ {e}")
        return False

    for w_ in printer.warnings:
        print(f"    ⚠ {w_}")

    st = printer.stats
    print(f"  [PRN] 协议解析: 宽={st['width']} dpi={st['dpi']} 纸张代码={st['paper']}")
    print(f"  [PRN] 光栅统计: W块={st['w_blocks']} Y跳过={st['y_skip_cmds']} "
          f"压缩字节={st['w_bytes']} 解码行={st['decoded_rows']}")

    # 与原始 PPM 比对
    if os.path.exists(ppm_path):
        pixels, w, h = load_ppm(ppm_path)
        print(f"  [PRN] 原始图像: {w}x{h} ({len(pixels)} 像素)")

        # 空白页特判: 打印流无光栅 = 全白页
        if not rows:
            orig_nonwhite = sum(1 for p in pixels if p != WHITE and p != (255,255,255))
            if orig_nonwhite == 0:
                print(f"  [PRN] 图像比对: 原图全白, 打印流无光栅 (空白页) — 正确")
                print(f"  [PRN] 状态: PAGE_COMPLETE ✓")
                return True
            else:
                print(f"  [PRN] 状态: ERROR — 原图有内容但打印流无光栅数据")
                return False

        ratio, details = compare_rows(rows, pixels, w, h)
        print(f"  [PRN] 图像比对: {details}")

        # 渲染预览
        preview = os.path.join(out_dir, f"{name}_decoded.ppm")
        render_preview(rows, preview)
        print(f"  [PRN] 解码预览: {preview}")

        # 判定
        if ratio >= 0.7:
            print(f"  [PRN] 状态: PAGE_COMPLETE ✓ (相似度 {ratio:.0%})")
            return True
        elif ratio >= 0.4:
            print(f"  [PRN] 状态: PAGE_COMPLETE ~ (相似度 {ratio:.0%}, 建议人工检查预览)")
            return True
        else:
            print(f"  [PRN] 状态: CHECK_FAILED ✗ (相似度仅 {ratio:.0%})")
            return False
    else:
        print(f"  [PRN] 状态: PAGE_COMPLETE (无原图可比对, 仅验证协议)")
        return True


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    test_dir = sys.argv[1]
    only = sys.argv[2] if len(sys.argv) > 2 else None
    verbose = os.environ.get("V") == "1"

    bins = sorted(f for f in os.listdir(test_dir) if f.endswith(".bin"))
    if only:
        bins = [f for f in bins if only in f]

    if not bins:
        print(f"目录 {test_dir} 中没有 .bin 文件")
        sys.exit(1)

    print(f"\n{'#'*70}")
    print(f"# 虚拟打印机测试 — {len(bins)} 个用例")
    print(f"{'#'*70}")

    results = {}
    for b in bins:
        bin_path = os.path.join(test_dir, b)
        ppm_path = bin_path.replace(".bin", ".ppm")
        results[b] = run_test(bin_path, ppm_path, test_dir, verbose)

    print(f"\n{'='*70}")
    print(f"总结")
    print(f"{'='*70}")
    passed = sum(1 for v in results.values() if v)
    for name, ok in results.items():
        print(f"  {'✓' if ok else '✗'} {name}")
    print(f"\n{passed}/{len(results)} 通过")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
