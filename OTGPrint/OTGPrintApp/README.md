# OTGPrint — 安卓 USB-OTG 直连打印机

安卓手机通过 USB-OTG 数据线**直连 HP DeskJet 2132 打印机**，不依赖系统打印服务、不依赖 PC 和官方驱动，APP 自己完成全部打印协议栈：文档/图片渲染 → PCL3-GUI 编码（Mode10 压缩）→ USB bulk-out 下发。

支持微信/QQ 分享的图片、PDF、Word 文档一键打印。

## 功能特性

- **图片打印**：JPG/PNG 等，等比缩放居中，支持 A4/Letter/4x6 纸张、300/600 DPI
- **PDF 打印**：系统 `PdfRenderer` 逐页渲染，按实际物理尺寸打印（非缩放），横向页自动旋转
- **Word 打印**：解析 `.docx`（段落/粗斜体/字号/颜色/表格/分页符），Canvas 排版绘制
- **微信/QQ 分享入口**：注册 `SEND` / `SEND_MULTIPLE` / `VIEW` intent-filter，聊天中的文件可直接「其他应用打开」打印；按文件头魔数识别类型（微信分享常无扩展名、mime 不准）
- **多页流式编码**：任务头 → 逐页编码 → 任务尾，边编边发，无需在内存拼接整个打印流
- **打印流导出**：可保存编码后的 `.bin` 打印流用于离线分析比对
- **本地验证框架**：虚拟打印机 + 测试流生成器，编解码 round-trip 全量回归（15/15 通过）

## 工作原理

HP DeskJet 2132 是 **host-based（主机渲染）** 打印机，只认 `PCL3-GUI` 光栅流，不解析 JPG/PDF/DOCX。打印链路：

```
图片 / PDF / DOCX
      │  (DocumentRenderer: PdfRenderer / XmlPullParser+Canvas)
      ▼
逐页 Bitmap (300dpi 渲染, 内存安全)
      │  (JNI → pcl3gui_encoder)
      ▼
PCL3-GUI 字节流
  ├─ PJL 任务头 (ENTER LANGUAGE=PCL3GUI)
  ├─ 每页: 页面设置(&l26A/*t600R/*r4891S/*p71Y…) + Mode10 压缩光栅 + *rC 换页
  └─ PJL EOJ 任务尾
      │  (UsbPrinterManager: bulkTransfer 16KB 分片)
      ▼
USB-OTG → HP DeskJet 2132 (VID=0x03F0 PID=0xE111)
```

关键协议参数（600dpi 基准，A4）：

| 参数 | 值 | 依据 |
|---|---|---|
| 光栅宽度 | 4891 dots | Windows 驱动抓包实测 |
| 顶边距 | 71 dots (3mm) | HP DJ2130 官方数据手册 |
| 底边距 | 300 dots (12.7mm) | 实测可打印至 6716 行无缺失 |
| 压缩 | Mode10 (Bert) | hplip Pcl3Gui2 参考实现 |

## 支持设备

- **打印机**：HP DeskJet 2132（抓包实测 VID=0x03F0 / PID=0xE111；同系列 2130/2131/2134/2136 理论兼容，需自行修改 [device_filter.xml](app/src/main/res/xml/device_filter.xml)）
- **手机**：Android 8.0+（minSdk 26），需支持 USB-Host
- **连接**：USB-OTG 线；打印机功耗较大，建议**有源供电的 OTG 拓展坞**，否则部分手机识别不稳

## 构建

环境要求：JDK 17、Android SDK (platform 34 / build-tools 34.0.0)、NDK 26.3、CMake 3.22.1、AGP 8.5.2 + Kotlin 1.9.24。

```bash
# Android Studio 直接打开本工程, 或命令行:
gradle assembleRelease
# 产物: app/build/outputs/apk/release/app-release.apk
```

**签名**：仓库不含 keystore。没有 keystore 时 release 自动退回 debug 签名，可直接构建调试。发布自己的版本请先生成密钥：

```bash
keytool -genkeypair -v -keystore otgprint.keystore -alias otgprint \
  -keyalg RSA -keysize 2048 -validity 10000
# 放到工程根目录, 密码通过环境变量 OTG_KS_PASS 注入 (不写进文件)
```

## 使用

1. 安装 APK，用 OTG 线连接打印机，按提示授予 USB 权限
2. **本地图片/PDF/Word**：APP 内选择文件 → 选纸张/DPI → 打印
3. **微信/QQ**：聊天中打开文件 → 右上角菜单 →「其他应用打开」→ 选「OTG直连打印」
4. 打印异常时：用「保存打印流」导出 `Documents/OTGPrint/android_output.bin`，与参考流逐字节比对排查

## 本地测试（无需打印机）

`tools/` 下是一套完整的离线验证框架，编码器任何改动都建议先跑回归：

```bash
cd tools
./run_local_tests.sh
# 生成 15 种测试流 → 虚拟打印机解码 → round-trip 比对
# 覆盖: 纯色/渐变/条纹/棋盘/半透明/噪声/空白/圆形/照片/多页/文档实际大小模式
```

- `gen_test_streams.cpp` — 生成各类 PCL3-GUI 测试流（与 APP 共用同一份编码器源码）
- `virtual_printer.py` — 虚拟打印机：解析打印流、Mode10 解码、还原图像、颜色比对
- `mode10_decode.py` — 独立 Mode10 解码器（逆向协议时用于交叉验证）

## 工程结构

```
OTGPrintApp/
├── app/src/main/
│   ├── cpp/
│   │   ├── pcl3gui_encoder.cpp/.h   # PCL3-GUI 编码核心 (Mode10 + 多页流式 API)
│   │   ├── jni_api.cpp              # JNI 桥
│   │   └── CMakeLists.txt
│   ├── java/com/otgprint/app/
│   │   ├── MainActivity.kt          # UI + 文件加载 + 打印流程
│   │   ├── DocumentRenderer.kt      # PDF/DOCX → 逐页 Bitmap
│   │   ├── PrintEngine.kt           # Kotlin 封装的编码入口
│   │   └── UsbPrinterManager.kt     # USB 枚举/权限/bulk 传输
│   ├── res/xml/device_filter.xml    # USB 设备过滤 (HP VID/PID)
│   └── AndroidManifest.xml          # 微信/QQ intent-filter
└── tools/                           # 离线测试框架 (见上)
```

## 版本历史

| 版本 | 内容 |
|---|---|
| 1.0 | 图片 USB-OTG 直连打印 |
| 1.1 | 新增 PDF / Word(.docx) 多页打印 |
| 1.2 | 修复 Word 解析失效（XmlPullParser 命名空间）；PDF/Word 改为实际尺寸打印；魔数识别文件类型；横向 PDF 自动旋转 |
| 1.3 | 修正打印边距（顶边距 31.9mm→3mm），文档接近 1:1 原尺寸、底部内容不再截断 |

## 已知限制

- 老版 `.doc` 二进制格式不支持（提示转存 `.docx`/PDF）
- `.docx` 排版为简化文本排版（自动换行/分页/基本格式），复杂版式（图片、艺术字、精确页眉页脚）不保证还 原
- A4 全出血页底部最多截 ~11.6mm（297mm 页高 > 284mm 可打印高，物理极限）；常规文档自带页边距，无截断
- 无边距打印不支持（该机型硬件不支持）

## 致谢

- [hplip](https://developers.hp.com/hp-linux-imaging-and-printing) — HP 官方开源打印栈，PCL3-GUI/Mode10 参考实现
- 协议参数通过 Windows 官方驱动 USB 抓包逆向 + 黄金样本比对验证

## 许可证

TODO: 选择你的开源许可证（如 MIT / Apache-2.0 / GPL-3.0）后替换此处并添加 LICENSE 文件。
