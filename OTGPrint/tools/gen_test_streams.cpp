/**
 * gen_test_streams.cpp — 本地测试: 生成多种测试图像 → 用真实编码器生成打印流
 *
 * 用途: 配合 virtual_printer.py 在本地验证打印流生成逻辑
 * 编译: clang++ -std=c++17 -I<encoder_dir> gen_test_streams.cpp pcl3gui_encoder.cpp -o gen_test
 * 运行: ./gen_test <output_dir>
 *
 * 每个测试用例输出:
 *   <name>.ppm   — 原始输入图像 (供解码后比对)
 *   <name>.bin   — PCL3-GUI 打印流 (虚拟打印机输入)
 */
#include "pcl3gui_encoder.h"
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <cmath>
#include <string>
#include <vector>
#include <random>

using namespace pcl3gui;

static void savePPM(const std::string& path, const std::vector<uint8_t>& rgba, int w, int h) {
    FILE* f = fopen(path.c_str(), "wb");
    if (!f) { fprintf(stderr, "无法创建 %s\n", path.c_str()); return; }
    fprintf(f, "P6\n%d %d\n255\n", w, h);
    for (int i = 0; i < w * h; i++) {
        fputc(rgba[i*4+0], f); fputc(rgba[i*4+1], f); fputc(rgba[i*4+2], f);
    }
    fclose(f);
}

static void saveBin(const std::string& path, const std::vector<uint8_t>& data) {
    FILE* f = fopen(path.c_str(), "wb");
    if (!f) { fprintf(stderr, "无法创建 %s\n", path.c_str()); return; }
    fwrite(data.data(), 1, data.size(), f);
    fclose(f);
}

struct TestCase {
    std::string name;
    std::string desc;
    int w, h;
    std::vector<uint8_t> rgba;
    PrintParams params;
};

// ---- 测试图像生成器 ----

// 1. 纯色 — 测试 RLE
static std::vector<uint8_t> genSolid(int w, int h, uint8_t r, uint8_t g, uint8_t b) {
    std::vector<uint8_t> rgba(w * h * 4);
    for (int i = 0; i < w * h; i++) {
        rgba[i*4+0]=r; rgba[i*4+1]=g; rgba[i*4+2]=b; rgba[i*4+3]=255;
    }
    return rgba;
}

// 2. 水平渐变 — 测试 literal delta 编码
static std::vector<uint8_t> genGradientH(int w, int h) {
    std::vector<uint8_t> rgba(w * h * 4);
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            int i = (y * w + x) * 4;
            rgba[i+0] = (uint8_t)(x * 255 / (w - 1));
            rgba[i+1] = (uint8_t)(y * 255 / (h - 1));
            rgba[i+2] = 128;
            rgba[i+3] = 255;
        }
    }
    return rgba;
}

// 3. 垂直条纹 — 测试颜色切换+seed copy
static std::vector<uint8_t> genStripes(int w, int h, int stripeW) {
    std::vector<uint8_t> rgba(w * h * 4);
    static const uint8_t colors[][3] = {
        {255,0,0},{0,255,0},{0,0,255},{255,255,0},{0,255,255},{255,0,255},{0,0,0},{255,255,255}
    };
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            int ci = (x / stripeW) % 8;
            int i = (y * w + x) * 4;
            rgba[i+0]=colors[ci][0]; rgba[i+1]=colors[ci][1]; rgba[i+2]=colors[ci][2]; rgba[i+3]=255;
        }
    }
    return rgba;
}

// 4. 棋盘格 — 测试快速颜色切换
static std::vector<uint8_t> genCheckerboard(int w, int h, int cell) {
    std::vector<uint8_t> rgba(w * h * 4);
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            bool on = ((x / cell) + (y / cell)) % 2 == 0;
            int i = (y * w + x) * 4;
            if (on) { rgba[i+0]=0; rgba[i+1]=0; rgba[i+2]=0; }
            else    { rgba[i+0]=255; rgba[i+1]=255; rgba[i+2]=254; }
            rgba[i+3]=255;
        }
    }
    return rgba;
}

// 5. 照片模拟(多种频率的正弦波叠加) — 测试压缩率
static std::vector<uint8_t> genPhotoLike(int w, int h) {
    std::vector<uint8_t> rgba(w * h * 4);
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            double fx = x / (double)w, fy = y / (double)h;
            int i = (y * w + x) * 4;
            rgba[i+0] = (uint8_t)(128 + 100 * sin(fx * 20.0) * cos(fy * 8.0));
            rgba[i+1] = (uint8_t)(128 + 100 * cos(fx * 5.0) * sin(fy * 15.0));
            rgba[i+2] = (uint8_t)(128 + 100 * sin((fx + fy) * 30.0));
            rgba[i+3] = 255;
        }
    }
    return rgba;
}

// 6. 随机噪声 — 压缩极限压力测试
static std::vector<uint8_t> genNoise(int w, int h, unsigned seed) {
    std::mt19937 rng(seed);
    std::uniform_int_distribution<int> dist(0, 255);
    std::vector<uint8_t> rgba(w * h * 4);
    for (int i = 0; i < w * h; i++) {
        rgba[i*4+0]=dist(rng); rgba[i*4+1]=dist(rng); rgba[i*4+2]=dist(rng); rgba[i*4+3]=255;
    }
    return rgba;
}

// 7. 半透明图像 — 测试 alpha 合成
static std::vector<uint8_t> genSemiTransparent(int w, int h) {
    std::vector<uint8_t> rgba(w * h * 4);
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            int i = (y * w + x) * 4;
            rgba[i+0] = 200; rgba[i+1] = 50; rgba[i+2] = 100;
            rgba[i+3] = (uint8_t)(x * 255 / (w - 1));  // 水平透明度渐变
        }
    }
    return rgba;
}

// 8. 空白(全白) — 测试空行跳过
static std::vector<uint8_t> genBlank(int w, int h) {
    return genSolid(w, h, 255, 255, 255);
}

// 9. 小图像 (信件尺寸照片打印常见)
static std::vector<uint8_t> genSmall(int w, int h) {
    std::vector<uint8_t> rgba(w * h * 4);
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            double cx = (x - w/2.0) / (w/2.0), cy = (y - h/2.0) / (h/2.0);
            double d = sqrt(cx*cx + cy*cy);
            int i = (y * w + x) * 4;
            if (d < 0.8) { rgba[i+0]=50; rgba[i+1]=120; rgba[i+2]=200; }
            else         { rgba[i+0]=250; rgba[i+1]=245; rgba[i+2]=240; }
            rgba[i+3]=255;
        }
    }
    return rgba;
}

// 多页流式编码测试: 模拟 APP 打印 PDF/Word 的路径
// prologue → encodePage()*N → epilogue 拼接
static void runMultiPageTests(const char* outDir, const PrintParams& params) {
    printf("=== 多页流式测试 (PDF/Word 打印路径) ===\n\n");

    // 3 页文档: 红 / 照片图案 / 蓝 (各页内容不同, 验证页间状态重置)
    struct MultiCase {
        const char* name;
        const char* desc;
        std::vector<std::vector<uint8_t>> pages;
    };

    auto solid = [](int w, int h, uint8_t r, uint8_t g, uint8_t b) {
        return genSolid(w, h, r, g, b);
    };

    std::vector<MultiCase> mcases;
    mcases.push_back({"20_multi_3pages", "3页文档 (红/照片/蓝)", {
        solid(120, 160, 180, 40, 40),
        genPhotoLike(120, 160),
        solid(120, 160, 40, 40, 180),
    }});

    // 5 页照片
    std::vector<std::vector<uint8_t>> photo5;
    for (int i = 0; i < 5; i++) photo5.push_back(genPhotoLike(120, 160));
    mcases.push_back({"21_multi_5pages", "5页照片", photo5});

    for (auto& mc : mcases) {
        std::string binPath = std::string(outDir) + "/" + mc.name + ".bin";
        std::string ppmPath = std::string(outDir) + "/" + mc.name + "_p0.ppm";

        // 保存第一页原图供比对
        if (!mc.pages.empty()) {
            savePPM(ppmPath, mc.pages[0], 120, 160);
        }

        // 流式拼接: prologue + N * page + epilogue
        std::vector<uint8_t> stream;
        auto prologue = buildJobPrologue();
        stream.insert(stream.end(), prologue.begin(), prologue.end());

        size_t pageOk = 0;
        for (auto& px : mc.pages) {
            // 页面尺寸都按 120x160 构造
            auto part = encodePage(px.data(), 120, 160, params);
            if (part.empty()) break;
            stream.insert(stream.end(), part.begin(), part.end());
            pageOk++;
        }
        auto epilogue = buildJobEpilogue();
        stream.insert(stream.end(), epilogue.begin(), epilogue.end());

        // 验证: 页数 = *rC 换页符数量
        int formFeeds = 0;
        for (size_t i = 0; i + 4 < stream.size(); i++) {
            if (stream[i] == 0x1B && stream[i+1] == '*' && stream[i+2] == 'r'
                && stream[i+3] == 'C' && stream[i+4] == 0x0C) {
                formFeeds++;
            }
        }

        saveBin(binPath, stream);
        printf("%-22s %-28s %zu/%zu页 %10zuB  换页符=%d %s\n\n",
               mc.name, mc.desc, pageOk, mc.pages.size(), stream.size(), formFeeds,
               (formFeeds == (int)pageOk && pageOk == mc.pages.size()) ? "[OK]" : "[FAIL]");
    }
}

// 文档「实际大小」模式测试: 模拟 APP 打印 PDF/Word 的路径
// (位图按 300dpi 渲染, srcDpi=300, 编码器 1:1 物理尺寸打印)
static void runDocModeTests(const char* outDir) {
    printf("=== 文档实际大小模式测试 (srcDpi=300, PDF/Word 打印路径) ===\n\n");

    struct DocCase {
        const char* name;
        const char* desc;
        int w, h;
        std::vector<uint8_t> rgba;
        int dpi;
        const char* expect;   // 期望的特征命令
    };

    // 半页 A4 @300dpi (105x148mm): 应 2x 放大到 600dpi, 宽度居中, 高度居中
    // (2480x3508 目标 < 可打印高 6645)
    // 用图案而非纯色, 顺便验证缩放采样
    std::vector<DocCase> dcases;
    dcases.push_back({"30_doc_half_a4", "半页A4文档@300dpi (居中)",
                      1240, 1754, genStripes(1240, 1754, 20), 600,
                      "\x1b*p71Y"});   // 顶边距 71 = 3mm (HP 数据手册)

    // 整页 A4 @300dpi: 高度超带宽 → 顶部对齐 + 底部截断
    dcases.push_back({"31_doc_full_a4", "整页A4文档@300dpi (顶对齐+截断)",
                      2480, 3508, genSolid(2480, 3508, 60, 60, 160), 600,
                      "\x1b*p71Y"});

    for (auto& dc : dcases) {
        std::string ppmPath = std::string(outDir) + "/" + dc.name + ".ppm";
        std::string binPath = std::string(outDir) + "/" + dc.name + ".bin";
        savePPM(ppmPath, dc.rgba, dc.w, dc.h);

        PrintParams params;
        params.dpi = dc.dpi;
        params.paper = PAPER_A4;
        params.srcDpi = 300;   // 文档实际大小模式

        auto stream = encodeImage(dc.rgba.data(), dc.w, dc.h, params);
        if (stream.empty()) {
            printf("%-22s %-32s [FAIL: 空流]\n\n", dc.name, dc.desc);
            continue;
        }
        saveBin(binPath, stream);

        // 验证文档模式特征: 顶边距 71 (3mm) + 光栅宽度
        bool hasTop = false;
        std::string topCmd = dc.expect;
        for (size_t i = 0; i + topCmd.size() <= stream.size(); i++) {
            if (memcmp(stream.data() + i, topCmd.data(), topCmd.size()) == 0) {
                hasTop = true;
                break;
            }
        }
        bool hasWidth = false;
        const char* widthCmd = dc.dpi == 600 ? "\x1b*r4891S" : "\x1b*r2445S";
        for (size_t i = 0; i + 8 <= stream.size(); i++) {
            if (memcmp(stream.data() + i, widthCmd, 8) == 0) { hasWidth = true; break; }
        }

        printf("%-22s %-32s %10zuB  *p71Y=%s *r%sS=%s %s\n\n",
               dc.name, dc.desc, stream.size(),
               hasTop ? "✓" : "✗", dc.dpi == 600 ? "4891" : "2445",
               hasWidth ? "✓" : "✗",
               (hasTop && hasWidth) ? "[OK]" : "[FAIL]");
    }
}

int main(int argc, char** argv) {
    const char* outDir = argc > 1 ? argv[1] : ".";
    std::vector<TestCase> cases;

    // ---- 小尺寸快速测试 ----
    cases.push_back({"01_solid_red", "纯红色 (RLE)", 100, 100, genSolid(100,100,200,30,30), {}});

    cases.push_back({"02_gradient", "水平+垂直渐变 (literal delta)", 120, 80, genGradientH(120,80), {}});

    cases.push_back({"03_stripes", "竖条纹 (颜色切换+seed)", 200, 100, genStripes(200,100,10), {}});

    cases.push_back({"04_checker", "棋盘格 (快速切换)", 160, 120, genCheckerboard(160,120,8), {}});

    cases.push_back({"05_semitrans", "半透明 (alpha合成)", 100, 60, genSemiTransparent(100,60), {}});

    cases.push_back({"06_noise", "随机噪声 (压缩极限)", 80, 60, genNoise(80,60,42), {}});

    cases.push_back({"07_blank", "全白 (空行跳过)", 100, 100, genBlank(100,100), {}});

    cases.push_back({"08_circle", "圆+背景 (小图)", 150, 100, genSmall(150,100), {}});

    // ---- 真实尺寸测试 (模拟照片打印) ----
    PrintParams p600; p600.dpi = 600; p600.paper = PAPER_A4;
    cases.push_back({"09_photo_a4_600", "照片模拟 A4@600dpi",
                     400, 300, genPhotoLike(400,300), p600});

    PrintParams p300; p300.dpi = 300; p300.paper = PAPER_A4;
    cases.push_back({"10_photo_a4_300", "照片模拟 A4@300dpi",
                     400, 300, genPhotoLike(400,300), p300});

    PrintParams p4x6; p4x6.dpi = 600; p4x6.paper = PAPER_4X6;
    cases.push_back({"11_photo_4x6", "照片模拟 4x6@600dpi",
                     300, 200, genPhotoLike(300,200), p4x6});

    // ---- 多页测试 (文档打印: PDF/Word 逐页渲染路径) ----
    runMultiPageTests(outDir, p600);

    // ---- 文档实际大小模式测试 (PDF/Word 1:1 物理尺寸) ----
    runDocModeTests(outDir);

    // ---- 执行编码 ----
    int pass = 0, fail = 0;
    printf("=== OTGPrint 打印流生成测试 ===\n\n");
    printf("%-22s %-28s %10s %12s  %s\n", "测试", "描述", "输入", "打印流", "状态");
    printf("%s\n", std::string(85, '-').c_str());

    for (auto& tc : cases) {
        std::string ppmPath = std::string(outDir) + "/" + tc.name + ".ppm";
        std::string binPath = std::string(outDir) + "/" + tc.name + ".bin";

        savePPM(ppmPath, tc.rgba, tc.w, tc.h);

        auto stream = encodeImage(tc.rgba.data(), tc.w, tc.h, tc.params);
        if (stream.empty()) {
            printf("%-22s %-28s %10s %12s  %s\n",
                   tc.name.c_str(), tc.desc.c_str(),
                   (std::to_string(tc.w) + "x" + std::to_string(tc.h)).c_str(),
                   "-", "[失败:空流]");
            fail++;
            continue;
        }

        saveBin(binPath, stream);

        double ratio = (double)stream.size() / (tc.w * tc.h * 3.0) * 100.0;
        printf("%-22s %-28s %10s %10zuB  [OK] (%.1f%%)\n",
               tc.name.c_str(), tc.desc.c_str(),
               (std::to_string(tc.w) + "x" + std::to_string(tc.h)).c_str(),
               stream.size(), ratio);
        pass++;
    }

    printf("%s\n", std::string(85, '-').c_str());
    printf("编码完成: %d 通过 / %d 失败\n", pass, fail);
    printf("输出目录: %s\n", outDir);
    printf("下一步: python3 virtual_printer.py %s\n", outDir);
    return fail > 0 ? 1 : 0;
}
