/**
 * pcl3gui_encoder.h — PCL3-GUI (Pcl3Gui2) 打印流编码器
 *
 * 从 hplip (HP 开源打印栈) 移植的 Mode10 (Bert Compression) 压缩算法，
 * 页面命令序列按 Windows 原厂驱动抓包样本 (reference_huipu_partial.bin)
 * 逐字节复刻，适配 HP DeskJet 2132 (VID=0x03F0 PID=0x9111)。
 *
 * 输入: RGBA_8888 位图像素 (Android Bitmap 内存字节序 R,G,B,A)
 * 输出: 完整 PCL3-GUI 打印流 (PJL 头 + PCL 设置 + Mode10 光栅 + 结尾)
 */
#ifndef PCL3GUI_ENCODER_H
#define PCL3GUI_ENCODER_H

#include <cstdint>
#include <cstddef>
#include <vector>

namespace pcl3gui {

// 纸张类型
enum PaperSize {
    PAPER_A4 = 0,
    PAPER_LETTER = 1,
    PAPER_4X6 = 2,
};

struct PrintParams {
    int dpi = 600;              // 300 或 600
    PaperSize paper = PAPER_A4;
    // 源位图 DPI。>0 = 文档「实际大小」模式: 1源像素 = dpi/srcDpi 打印点,
    //   从顶边距起排, 超出可打印高度的内容截断 (PDF/Word 页面按真实尺寸打印);
    // 0 = 照片「适应可打印区」模式: 等比缩放居中 (v1.0 行为, 不变)。
    int srcDpi = 0;
};

// 纸张几何信息 (以 600 DPI 为基准, 实际按 dpi 缩放)
struct PaperGeometry {
    const char* name;
    int pclId;        // PCL &l#A 纸张代码 (A4=26, Letter=2, 4x6=72?)
    int totalW;       // 600dpi 总宽 (dots)
    int totalH;       // 600dpi 总高
    int printableW;   // 可打印宽
    int topMargin;    // 上边距 (黄金样本: 754+82=836 dots @600dpi)
    int bottomMargin; // 下边距
};

const PaperGeometry& paperGeometry(PaperSize paper);

/**
 * 主编码入口 (单页)。
 * rgba: srcW*srcH*4 字节, 每像素 R,G,B,A 字节序 (Android ARGB_8888)。
 * 返回完整打印流。失败返回空 vector。
 */
std::vector<uint8_t> encodeImage(const uint8_t* rgba,
                                 int srcW, int srcH,
                                 const PrintParams& params);

/** 仅生成任务头+页设置+结尾(无光栅), 用于测试/空白页 */
std::vector<uint8_t> buildJobSkeleton(const PrintParams& params);

// ---- 多页流式 API (文档打印: PDF/Word 每页一个位图) ----
// 用法: buildJobPrologue() → encodePage()*N → buildJobEpilogue(),
//       顺序拼接即为完整多页打印流。
// 页分隔: 每页 encodePage 自带 *rC + 换页符; 下一页重发页面设置
//        (与 hplip Pcl3Gui2 的 BeginPage/FormFeed 行为一致)。
std::vector<uint8_t> buildJobPrologue();
std::vector<uint8_t> encodePage(const uint8_t* rgba, int srcW, int srcH,
                                const PrintParams& params);
std::vector<uint8_t> buildJobEpilogue();

} // namespace pcl3gui

#endif // PCL3GUI_ENCODER_H
