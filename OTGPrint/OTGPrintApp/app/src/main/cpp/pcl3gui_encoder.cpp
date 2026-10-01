/**
 * pcl3gui_encoder.cpp — Mode10 压缩 + PCL3-GUI 流组装
 *
 * Mode10 算法忠实移植自 hplip/prnt/hpcups/Mode10.cpp (HP 开源, BSD 许可)。
 * 命令序列复刻自 Windows 原厂驱动 USB 抓包黄金样本:
 *   &l0S &l1H *o5W(0D 03 00 03 EC) *o0M &l26A &l0M *o5W(0B 01 00 00 00)
 *   &u600D *t600R *g12W(CRD color-only Mode10) *r4891S &l-2H *r1A *p754Y
 *   [*b#Y 空行跳过] [*b#W Mode10光栅行]... *rC FF E UEL
 */
#include "pcl3gui_encoder.h"
#include <cstdio>
#include <cstring>
#include <algorithm>
#include <cmath>

namespace pcl3gui {

static const uint32_t kWhite = 0x00FFFFFEu;  // 掩码后的白色 (255,255,254)

// ---------------------------------------------------------------------------
// 纸张几何 (600dpi 基准值)
// ---------------------------------------------------------------------------
static const PaperGeometry kPapers[] = {
    // name        pclId totalW totalH printableW topMargin bottomMargin
    //
    // 顶边距 71 dots = 3mm: HP DJ2130 系列官方数据手册 "Print margins:
    //   Top 3mm; Bottom 14.5mm; Left/Right 3mm"。黄金样本的 *p754Y (31.9mm)
    //   是那次打印任务的应用层图片定位 (照片墨迹从 754+82 行才开始),
    //   不是硬件边距 —— v1.2 误用作文档顶边距导致页首多留 29mm 空白。
    // 底边距 300 dots = 12.7mm: v1.1 整幅 PDF 实测打印到 6716 行内容完整
    //   (数据手册 14.5mm 偏保守, 黄金驱动实际用到 4891 宽也超出数据手册标注)。
    { "A4",         26,   4961,  7016,   4891,      71,       300 },
    { "Letter",      2,   5100,  6600,   5030,      71,       300 },
    { "4x6",        72,   2400,  3600,   2330,      71,       300 },
};

const PaperGeometry& paperGeometry(PaperSize paper) {
    return kPapers[static_cast<int>(paper)];
}

// ---------------------------------------------------------------------------
// 字节缓冲追加辅助
// ---------------------------------------------------------------------------
class Buf {
public:
    std::vector<uint8_t> v;
    void u8(uint8_t b) { v.push_back(b); }
    void bytes(const void* p, size_t n) {
        const uint8_t* q = static_cast<const uint8_t*>(p);
        v.insert(v.end(), q, q + n);
    }
    void str(const char* s) { bytes(s, strlen(s)); }
    void be16(uint32_t x) { u8((x >> 8) & 0xFF); u8(x & 0xFF); }
};

// ---------------------------------------------------------------------------
// Mode10 压缩器 (移植自 hplip Mode10.cpp)
// ---------------------------------------------------------------------------
class Mode10 {
public:
    explicit Mode10(int pixelWidth)
        : width_(pixelWidth),
          planeSize_(static_cast<size_t>(pixelWidth) * 3),
          compressBuf_(planeSize_ + planeSize_ / 2 + 16),
          workRow_(planeSize_),
          seedRow_(planeSize_, 0xFF) {}

    // 编码一行 (row = width*3 字节 RGB)。返回压缩后长度; 0 表示整行与 seed 相同。
    size_t encodeRow(const uint8_t* row) {
        // 工作副本: sentinel 会修改最后一个像素
        memcpy(workRow_.data(), row, planeSize_);
        uint8_t* curRow = workRow_.data();
        uint8_t* seed = seedRow_.data();
        uint8_t* out = compressBuf_.data();

        const uint32_t lastPixel = static_cast<uint32_t>(width_ - 1);

        // Sentinel: 让最后像素与左邻/上方seed都不同, 简化结束条件
        uint32_t realLastPixel = getPixel(curRow, lastPixel);
        uint32_t newLastPixel = realLastPixel;
        while (getPixel(curRow, lastPixel - 1) == newLastPixel ||
               getPixel(seed, lastPixel) == newLastPixel) {
            newLastPixel += 0x100;  // green+1
            putPixel(curRow, lastPixel, newLastPixel);
        }

        uint32_t curPixel = 0;
        uint32_t cachedColor = kWhite;
        bool done = false;

        do {
            uint8_t CMDByte = 0;
            int replacementCount = 0;
            int pixelSource = 0;  // eeNewPixel

            // ---- seed 行相同像素复制计数 ----
            uint32_t start = curPixel;
            while (getPixel(seed, curPixel) == getPixel(curRow, curPixel)) {
                curPixel++;
            }
            uint32_t seedRowPixelCopyCount = curPixel - start;
            // (sentinel 保证 curPixel <= lastPixel)

            if (curPixel == lastPixel) {
                // ---- 位于行尾像素 ----
                putPixel(curRow, lastPixel, realLastPixel);
                if (getPixel(seed, curPixel) == realLastPixel) {
                    done = true;  // 整行结束, 尾像素与seed相同无需编码
                    break;
                }
                CMDByte = 0x00;      // eLiteral
                pixelSource = 0x00;  // eeNewPixel
                replacementCount = 1;
                curPixel++;
            } else {
                // ---- RLE 检测 ----
                replacementCount = static_cast<int>(curPixel);
                uint32_t RLERun = getPixel(curRow, curPixel);
                curPixel++;
                while (RLERun == getPixel(curRow, curPixel)) {
                    curPixel++;
                }
                curPixel--;  // 退回当前
                replacementCount = static_cast<int>(curPixel) - replacementCount;

                if (replacementCount > 0) {
                    curPixel++;
                    replacementCount++;

                    if (cachedColor == RLERun) {
                        pixelSource = 0x60;  // eeCachedColor
                    } else if (getPixel(seed, curPixel - replacementCount + 1) == RLERun) {
                        pixelSource = 0x40;  // eeNEPixel
                    } else if ((curPixel - replacementCount > 0) &&
                               getPixel(curRow, curPixel - replacementCount - 1) == RLERun) {
                        pixelSource = 0x20;  // eeWPixel
                    } else {
                        pixelSource = 0x00;  // eeNewPixel
                        cachedColor = RLERun;
                    }
                    CMDByte = 0x80;  // eRLE
                }

                if (curPixel == lastPixel && replacementCount > 0) {
                    // RLE 紧邻最后像素
                    if (realLastPixel == RLERun) {
                        putPixel(curRow, lastPixel, realLastPixel);
                        replacementCount++;
                        curPixel++;
                    }
                }

                if (replacementCount == 0) {
                    // ---- Literal run ----
                    uint32_t tempPixel = getPixel(curRow, curPixel);

                    CMDByte = 0x00;  // eLiteral
                    if (cachedColor == tempPixel) {
                        pixelSource = 0x60;
                    } else if (getPixel(seed, curPixel + 1) == tempPixel) {
                        pixelSource = 0x40;
                    } else if ((curPixel > 0) &&
                               getPixel(curRow, curPixel - 1) == tempPixel) {
                        pixelSource = 0x20;
                    } else {
                        pixelSource = 0x00;
                        cachedColor = tempPixel;
                    }

                    replacementCount = static_cast<int>(curPixel);
                    uint32_t cachePixel;
                    uint32_t nextPixel = getPixel(curRow, curPixel + 1);
                    do {
                        if (++curPixel == lastPixel) {
                            putPixel(curRow, lastPixel, realLastPixel);
                            curPixel++;
                            break;
                        }
                        cachePixel = nextPixel;
                    } while ((cachePixel != (nextPixel = getPixel(curRow, curPixel + 1))) &&
                             (cachePixel != getPixel(seed, curPixel)));
                    replacementCount = static_cast<int>(curPixel) - replacementCount;
                }
            }

            // ================= 输出压缩数据 =================
            if (CMDByte == 0x00) {
                // Literal
                replacementCount -= 1;  // 归一化
                CMDByte = static_cast<uint8_t>(
                    pixelSource |
                    (std::min(3, static_cast<int>(seedRowPixelCopyCount)) << 3) |
                    std::min(7, replacementCount));
                *out++ = CMDByte;

                if (seedRowPixelCopyCount >= 3) {
                    out = outputVLI(out, static_cast<int>(seedRowPixelCopyCount) - 3);
                }

                replacementCount += 1;
                const int totalReplacementCount = replacementCount;
                int upwardPixelCount = 1;

                if (pixelSource != 0x00) {
                    replacementCount -= 1;  // 首像素来自替代源不编码
                    upwardPixelCount = 2;
                }

                for (; upwardPixelCount <= totalReplacementCount; upwardPixelCount++) {
                    uint32_t cur = getPixel(curRow, curPixel - replacementCount);
                    uint32_t sed = getPixel(seed, curPixel - replacementCount);
                    uint16_t delta = shortDelta(cur, sed);
                    if (delta) {
                        *out++ = static_cast<uint8_t>(delta >> 8);
                        *out++ = static_cast<uint8_t>(delta & 0xFF);
                    } else {
                        uint32_t up = cur >> 1;
                        *out++ = static_cast<uint8_t>((up >> 16) & 0xFF);
                        *out++ = static_cast<uint8_t>((up >> 8) & 0xFF);
                        *out++ = static_cast<uint8_t>(up & 0xFF);
                    }
                    if (((upwardPixelCount - 8) % 255) == 0) {
                        *out++ = static_cast<uint8_t>(
                            std::min(255, totalReplacementCount - upwardPixelCount));
                    }
                    replacementCount--;
                }
            } else {
                // RLE
                replacementCount -= 2;  // 归一化
                CMDByte = static_cast<uint8_t>(
                    0x80 |  // eRLE 类型位
                    pixelSource |
                    (std::min(3, static_cast<int>(seedRowPixelCopyCount)) << 3) |
                    std::min(7, replacementCount));
                *out++ = CMDByte;

                if (seedRowPixelCopyCount >= 3) {
                    out = outputVLI(out, static_cast<int>(seedRowPixelCopyCount) - 3);
                }

                replacementCount += 2;

                if (pixelSource == 0x00) {
                    uint32_t cur = getPixel(curRow, curPixel - replacementCount);
                    uint32_t sed = getPixel(seed, curPixel - replacementCount);
                    uint16_t delta = shortDelta(cur, sed);
                    if (delta) {
                        *out++ = static_cast<uint8_t>(delta >> 8);
                        *out++ = static_cast<uint8_t>(delta & 0xFF);
                    } else {
                        uint32_t up = cur >> 1;
                        *out++ = static_cast<uint8_t>((up >> 16) & 0xFF);
                        *out++ = static_cast<uint8_t>((up >> 8) & 0xFF);
                        *out++ = static_cast<uint8_t>(up & 0xFF);
                    }
                }

                if (replacementCount - 2 >= 7) {
                    out = outputVLI(out, replacementCount - (7 + 2));
                }
            }
        } while (curPixel <= lastPixel && !done);

        // seed = 当前行 (尾像素已在各分支恢复真实值)
        memcpy(seedRow_.data(), workRow_.data(), planeSize_);
        return static_cast<size_t>(out - compressBuf_.data());
    }

    // 空行跳过后重置 seed (对应 hplip Pipeline::Flush -> Mode10::Flush)
    void flush() {
        std::fill(seedRow_.begin(), seedRow_.end(), 0xFF);
    }

    const uint8_t* data() const { return compressBuf_.data(); }

private:
    static inline uint32_t getPixel(const uint8_t* p, int off) {
        const uint8_t* q = p + off * 3;
        return kWhite & ((uint32_t(q[0]) << 16) | (uint32_t(q[1]) << 8) | uint32_t(q[2]));
    }
    static inline void putPixel(uint8_t* p, int off, uint32_t pixel) {
        uint8_t* q = p + off * 3;
        uint32_t t = pixel & kWhite;
        q[0] = static_cast<uint8_t>((t >> 16) & 0xFF);
        q[1] = static_cast<uint8_t>((t >> 8) & 0xFF);
        q[2] = static_cast<uint8_t>(t & 0xFF);
    }

    // 短delta: cur 与 seed 的差值落在 5bit 有符号范围时输出 2 字节编码
    static inline uint16_t shortDelta(uint32_t cur, uint32_t sed) {
        int dr = static_cast<int>((cur >> 16) & 0xFF) - static_cast<int>((sed >> 16) & 0xFF);
        int dg = static_cast<int>((cur >> 8) & 0xFF) - static_cast<int>((sed >> 8) & 0xFF);
        int db = static_cast<int>(cur & 0xFF) - static_cast<int>(sed & 0xFF);
        if (dr <= 15 && dr >= -16 && dg <= 15 && dg >= -16 && db <= 30 && db >= -32) {
            // db 除以2扩展范围 (算术移位), dr/dg 5bit 补码
            return static_cast<uint16_t>(
                0x8000u |
                ((uint32_t(dr) & 0x1Fu) << 10) |
                ((uint32_t(dg) & 0x1Fu) << 5) |
                ((uint32_t(db) >> 1) & 0x1Fu));
        }
        return 0;
    }

    // hplip outputVLIBytesConsecutively 的忠实移植:
    // 255 重复, 余数结尾; 剩余恰好255时补一个0终止
    static inline uint8_t* outputVLI(uint8_t* p, int number) {
        do {
            int m = std::min(number, 255);
            *p++ = static_cast<uint8_t>(m);
            if (number == 255) {
                *p++ = 0;
            }
            number -= m;
        } while (number);
        return p;
    }

    int width_;
    size_t planeSize_;
    std::vector<uint8_t> compressBuf_;
    std::vector<uint8_t> workRow_;
    std::vector<uint8_t> seedRow_;
};

// ---------------------------------------------------------------------------
// PCL3-GUI 任务流组装 (命令序列复刻 Windows 黄金样本)
// 结构: prologue(每任务一次) + [pageSetup + 光栅 + pageEnd](每页) + epilogue(每任务一次)
// 多页格式与 hplip Pcl3Gui2 的 BeginPage/FormFeed 行为一致
// ---------------------------------------------------------------------------
static void buildJobPrologue(Buf& out) {
    static const uint8_t kReset[] = {0x1B, 'E'};
    static const uint8_t kUEL[] = {0x1B, '%', '-', '1', '2', '3', '4', '5', 'X'};
    out.bytes(kReset, sizeof(kReset));
    out.bytes(kUEL, sizeof(kUEL));
    out.str("@PJL SET STRINGCODESET=UTF8\n");
    out.str("@PJL JOB NAME=\"OTGPrint\"\n");
    out.str("@PJL COMMENT=\"OTGPrint Android USB Direct Print\"\n");
    out.str("@PJL ENTER LANGUAGE=PCL3GUI\n");
    out.bytes(kReset, sizeof(kReset));      // 黄金样本: ENTER LANGUAGE 后再次复位
}

// 页面设置 (每页重发, &l0S ... *p#Y, 与黄金样本逐字节对应)
static void buildPageSetup(Buf& out, const PaperGeometry& geo,
                           int scaledDpi, int printableW, int topMargin) {
    out.str("\x1b&l0S");                 // 单面打印
    out.str("\x1b&l1H");                 // 纸源: 进纸盒
    // 媒体子类型: 黄金样本 0D 03 00 03 EC
    out.str("\x1b*o5W");
    out.bytes("\x0D\x03\x00\x03\xEC", 5);
    out.str("\x1b*o0M");                 // 打印质量
    char tmp[64];
    snprintf(tmp, sizeof(tmp), "\x1b&l%dA", geo.pclId);  // 纸张尺寸
    out.str(tmp);
    out.str("\x1b&l0M");                 // 纸张类型: 普通纸
    out.str("\x1b*o5W");
    out.bytes("\x0B\x01\x00\x00\x00", 5);  // 彩色模式
    snprintf(tmp, sizeof(tmp), "\x1b&u%dD", scaledDpi);  // 度量单位
    out.str(tmp);
    snprintf(tmp, sizeof(tmp), "\x1b*t%dR", scaledDpi);  // 光栅分辨率
    out.str(tmp);
    // CRD: color-only 平面, Mode10 压缩 (黄金样本 *g12W 12字节, 分辨率按DPI替换)
    out.str("\x1b*g12W");
    out.u8(0x06); out.u8(0x07); out.u8(0x00); out.u8(0x01);
    out.be16(static_cast<uint32_t>(scaledDpi));
    out.be16(static_cast<uint32_t>(scaledDpi));
    out.u8(0x0A); out.u8(0x01); out.u8(0x20); out.u8(0x01);
    snprintf(tmp, sizeof(tmp), "\x1b*r%dS", printableW);  // 光栅宽度
    out.str(tmp);
    out.str("\x1b&l-2H");                // 媒体预载
    out.str("\x1b*r1A");                 // 开始光栅
    snprintf(tmp, sizeof(tmp), "\x1b*p%dY", topMargin);  // 光栅起始行
    out.str(tmp);
}

// 页结束: 结束光栅 + 走纸 (hplip Encapsulator::FormFeed)
static void buildPageEnd(Buf& out) {
    out.str("\x1b*rC\x0C");
}

// 任务结束: 复位 + UEL + PJL EOJ + UEL
static void buildJobEpilogue(Buf& out) {
    static const uint8_t kReset[] = {0x1B, 'E'};
    static const uint8_t kUEL[] = {0x1B, '%', '-', '1', '2', '3', '4', '5', 'X'};
    out.bytes(kReset, sizeof(kReset));
    out.bytes(kUEL, sizeof(kUEL));
    out.str("@PJL EOJ\n");
    out.bytes(kUEL, sizeof(kUEL));
}

std::vector<uint8_t> buildJobSkeleton(const PrintParams& params) {
    const PaperGeometry& geo = paperGeometry(params.paper);
    // dpi 缩放 (300/600), 用浮点避免整数除法截断为0
    const int printableW = static_cast<int>(int64_t(geo.printableW) * params.dpi / 600);
    Buf out;
    buildJobPrologue(out);
    buildPageSetup(out, geo, params.dpi, printableW,
                   static_cast<int>(int64_t(geo.topMargin) * params.dpi / 600));
    buildPageEnd(out);
    buildJobEpilogue(out);
    return std::move(out.v);
}

// ---------------------------------------------------------------------------
// 图像缩放 + 行生成 + 光栅编码
// ---------------------------------------------------------------------------

// 页面布局: 统一计算光栅宽度 / 起始行 / 行数 / 缩放与偏移
struct PageLayout {
    int printableW;  // 光栅宽度 (*r#S)
    int topMargin;   // 光栅起始行 (*p#Y)
    int bandH;       // 光栅行数 (topMargin 到下边距)
    double scale;    // 源像素 -> 打印点
    int dstW, dstH;  // 缩放后目标尺寸
    int offX, offY;  // 目标在光栅区中的偏移
};

static PageLayout computeLayout(int srcW, int srcH, const PrintParams& params) {
    const PaperGeometry& geo = paperGeometry(params.paper);
    PageLayout L;
    L.printableW = static_cast<int>(int64_t(geo.printableW) * params.dpi / 600);
    const bool actual = params.srcDpi > 0;
    // 顶边距统一用表值 (71 dots = 3mm, HP 数据手册硬件打印边距)
    L.topMargin = static_cast<int>(int64_t(geo.topMargin) * params.dpi / 600);
    L.bandH = static_cast<int>(
        int64_t(geo.totalH - geo.topMargin - geo.bottomMargin) * params.dpi / 600);

    if (actual) {
        // 实际大小: 1 源像素 = dpi/srcDpi 打印点; 超出可打印宽则缩到
        // 可打印宽 (横向页自动适配纵向纸), 高度超出部分底部截断。
        // A4 全出血页 @300dpi 渲染: 宽限缩放 98.6%, 底部截 ~11.6mm
        // (A4 高 297mm > 可打印 284mm, 物理极限); 常规文档自带页边距,
        // 底边距 >= 12mm 时内容零截断。
        double s = double(params.dpi) / params.srcDpi;
        if (double(srcW) * s > L.printableW) s = double(L.printableW) / srcW;
        L.scale = s;
        L.dstW = std::min(L.printableW, std::max(1, int(srcW * s + 0.5)));
        L.dstH = std::max(1, int(srcH * s + 0.5));
    } else {
        // 适应可打印区 (保持纵横比, 居中) — 照片模式
        double s = std::min(double(L.printableW) / srcW, double(L.bandH) / srcH);
        L.scale = s;
        L.dstW = std::max(1, int(srcW * s + 0.5));
        L.dstH = std::max(1, int(srcH * s + 0.5));
        if (L.dstW > L.printableW) L.dstW = L.printableW;
        if (L.dstH > L.bandH) L.dstH = L.bandH;
    }
    L.offX = (L.printableW - L.dstW) / 2;
    L.offY = (L.bandH - L.dstH) / 2;   // 文档模式 dstH>=bandH 时 <=0 → 顶部对齐
    if (L.offY < 0) L.offY = 0;
    return L;
}

// 光栅主体: 缩放并编码一页位图 (不含页面设置和页结束命令)
static void encodeRaster(Buf& out, const uint8_t* rgba,
                         int srcW, int srcH, const PrintParams& params) {
    const PageLayout L = computeLayout(srcW, srcH, params);
    const int printableW = L.printableW;
    const int printableH = L.bandH;
    const double s = L.scale;
    const int dstW = L.dstW;
    const int dstH = L.dstH;
    const int offX = L.offX;
    const int offY = L.offY;

    Mode10 mode10(printableW);
    std::vector<uint8_t> rowBuf(static_cast<size_t>(printableW) * 3);
    // 白色行模板 (kWhite: FF FF FE)
    std::vector<uint8_t> blankRow(static_cast<size_t>(printableW) * 3);
    for (int x = 0; x < printableW; x++) {
        blankRow[x * 3] = 0xFF;
        blankRow[x * 3 + 1] = 0xFF;
        blankRow[x * 3 + 2] = 0xFE;
    }

    // 预计算 x 方向源坐标 (像素中心对齐, 双线性)
    std::vector<double> srcX(printableW);
    std::vector<int> srcX0(printableW);
    std::vector<double> fx(printableW);
    for (int x = 0; x < printableW; x++) {
        double sx = (x - offX + 0.5) / s - 0.5;
        if (sx < 0) sx = 0;
        if (sx > srcW - 1) sx = srcW - 1;
        srcX[x] = sx;
        srcX0[x] = int(sx);
        if (srcX0[x] > srcW - 2) srcX0[x] = srcW - 2;
        if (srcX0[x] < 0) srcX0[x] = 0;
        fx[x] = sx - srcX0[x];
    }

    char cmd[32];
    int skipCount = 0;

    for (int y = 0; y < printableH; y++) {
        const uint8_t* rowPixels;
        bool blank = false;

        if (y < offY || y >= offY + dstH) {
            blank = true;
            rowPixels = blankRow.data();
        } else {
            // 目标行 -> 源行 (双线性)
            double sy = (y - offY + 0.5) / s - 0.5;
            if (sy < 0) sy = 0;
            if (sy > srcH - 1) sy = srcH - 1;
            int y0 = int(sy);
            if (y0 > srcH - 2) y0 = srcH - 2;
            if (y0 < 0) y0 = 0;
            double fy = sy - y0;

            const uint8_t* r0 = rgba + size_t(y0) * srcW * 4;
            const uint8_t* r1 = rgba + size_t(y0 + 1) * srcW * 4;

            uint8_t* dst = rowBuf.data();
            for (int x = 0; x < printableW; x++) {
                if (x < offX || x >= offX + dstW) {
                    dst[0] = 0xFF; dst[1] = 0xFF; dst[2] = 0xFE;
                } else {
                    int x0 = srcX0[x];
                    double wX = fx[x];
                    // 双线性 4 texel (RGBA), alpha 合成到白底
                    const uint8_t* p00 = r0 + size_t(x0) * 4;
                    const uint8_t* p10 = r0 + size_t(x0 + 1) * 4;
                    const uint8_t* p01 = r1 + size_t(x0) * 4;
                    const uint8_t* p11 = r1 + size_t(x0 + 1) * 4;
                    double w00 = (1 - wX) * (1 - fy);
                    double w10 = wX * (1 - fy);
                    double w01 = (1 - wX) * fy;
                    double w11 = wX * fy;
                    double R = 0, G = 0, B = 0, A = 0;
                    auto acc = [&](const uint8_t* p, double w) {
                        R += double(p[0]) * w;
                        G += double(p[1]) * w;
                        B += double(p[2]) * w;
                        A += double(p[3]) * w;
                    };
                    acc(p00, w00); acc(p10, w10); acc(p01, w01); acc(p11, w11);
                    double a = A / 255.0;
                    // 权重浮点误差可能使 a 微超 [0,1], (1-a) 为负会放大成
                    // 整数偏差 (如 200→199), 必须 clamp
                    if (a < 0.0) a = 0.0;
                    if (a > 1.0) a = 1.0;
                    R = R * a + 255.0 * (1 - a);
                    G = G * a + 255.0 * (1 - a);
                    B = B * a + 255.0 * (1 - a);
                    auto clamp255 = [](double v) {
                        return v < 0 ? 0 : (v > 255 ? 255 : int(v + 0.5));
                    };
                    dst[0] = uint8_t(clamp255(R));
                    dst[1] = uint8_t(clamp255(G));
                    dst[2] = uint8_t(clamp255(B) & 0xFE);  // kWhite 掩码: 蓝色最低位清零
                }
                dst += 3;
            }
            rowPixels = rowBuf.data();

            // 空行检测
            blank = (memcmp(rowPixels, blankRow.data(), rowBuf.size()) == 0);
        }

        if (blank) {
            skipCount++;
            continue;
        }

        // 空行跳过: 先 flush 压缩器(重置seed), 发 *b#Y
        if (skipCount > 0) {
            mode10.flush();
            snprintf(cmd, sizeof(cmd), "\x1b*b%dY", skipCount);
            out.str(cmd);
            skipCount = 0;
        }

        size_t csize = mode10.encodeRow(rowPixels);
        snprintf(cmd, sizeof(cmd), "\x1b*b%zuW", csize);
        out.str(cmd);
        if (csize > 0) {
            out.bytes(mode10.data(), csize);
        }
    }
}

// ---------------------------------------------------------------------------
// 公开 API: 单页 & 多页流式
// ---------------------------------------------------------------------------
std::vector<uint8_t> buildJobPrologue() {
    Buf out;
    buildJobPrologue(out);
    return std::move(out.v);
}

std::vector<uint8_t> buildJobEpilogue() {
    Buf out;
    buildJobEpilogue(out);
    return std::move(out.v);
}

std::vector<uint8_t> encodePage(const uint8_t* rgba, int srcW, int srcH,
                                const PrintParams& params) {
    if (!rgba || srcW <= 0 || srcH <= 0) return {};
    if (params.dpi != 300 && params.dpi != 600) return {};

    const PaperGeometry& geo = paperGeometry(params.paper);
    const PageLayout L = computeLayout(srcW, srcH, params);

    Buf out;
    buildPageSetup(out, geo, params.dpi, L.printableW, L.topMargin);
    encodeRaster(out, rgba, srcW, srcH, params);
    buildPageEnd(out);
    return std::move(out.v);
}

std::vector<uint8_t> encodeImage(const uint8_t* rgba,
                                 int srcW, int srcH,
                                 const PrintParams& params) {
    if (!rgba || srcW <= 0 || srcH <= 0) return {};
    if (params.dpi != 300 && params.dpi != 600) return {};

    Buf out;
    buildJobPrologue(out);
    auto page = encodePage(rgba, srcW, srcH, params);
    if (page.empty()) return {};
    out.bytes(page.data(), page.size());
    buildJobEpilogue(out);
    return std::move(out.v);
}

} // namespace pcl3gui
