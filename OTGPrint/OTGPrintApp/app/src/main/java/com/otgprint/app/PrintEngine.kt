package com.otgprint.app

import android.graphics.Bitmap
import android.graphics.Color

/**
 * PCL3-GUI 编码引擎 (JNI 封装)
 *
 * 流程: Bitmap -> RGBA字节数组 -> JNI C++ Mode10压缩+PCL3GUI流组装 -> ByteArray
 */
object PrintEngine {

    init {
        System.loadLibrary("otgprint")
    }

    /** 纸张类型 (与 C++ PaperSize 枚举对应) */
    const val PAPER_A4 = 0
    const val PAPER_LETTER = 1
    const val PAPER_4X6 = 2

    // DPI 支持列表
    val SUPPORTED_DPI = intArrayOf(300, 600)

    private external fun nativeEncode(
        rgba: ByteArray, srcW: Int, srcH: Int,
        dpi: Int, paper: Int
    ): ByteArray?

    // ---- 多页流式接口 (文档打印: PDF/Word) ----
    private external fun nativeBeginJob(): ByteArray?
    private external fun nativeEncodePage(
        rgba: ByteArray, srcW: Int, srcH: Int,
        dpi: Int, paper: Int, srcDpi: Int
    ): ByteArray?
    private external fun nativeEndJob(): ByteArray?

    /** Bitmap -> RGBA 字节 (R,G,B,A 内存序) */
    private fun bitmapToRgba(bitmap: Bitmap): ByteArray {
        val w = bitmap.width
        val h = bitmap.height
        val pixels = IntArray(w * h)
        bitmap.getPixels(pixels, 0, w, 0, 0, w, h)
        val rgba = ByteArray(w * h * 4)
        var i = 0
        for (p in pixels) {
            rgba[i++] = Color.red(p).toByte()
            rgba[i++] = Color.green(p).toByte()
            rgba[i++] = Color.blue(p).toByte()
            rgba[i++] = Color.alpha(p).toByte()
        }
        return rgba
    }

    /**
     * 编码位图为完整 PCL3-GUI 打印流。
     * @param bitmap ARGB_8888 位图 (自动转换)
     * @param dpi 300 / 600
     * @param paper PrintEngine.PAPER_*
     * @return 完整打印流字节, 失败返回 null
     */
    fun encode(bitmap: Bitmap, dpi: Int, paper: Int): ByteArray? {
        if (dpi !in SUPPORTED_DPI) return null
        val w = bitmap.width
        val h = bitmap.height
        if (w <= 0 || h <= 0) return null
        val rgba = bitmapToRgba(bitmap)
        return nativeEncode(rgba, w, h, dpi, paper)
    }

    /** 多页任务头 (reset + UEL + PJL) */
    fun beginJob(): ByteArray = nativeBeginJob()
        ?: throw IllegalStateException("编码任务头失败")

    /**
     * 编码单页 (页面设置 + Mode10 光栅 + 换页)。
     * 多页文档逐页调用, 与 beginJob/endJob 顺序拼接。
     *
     * @param srcDpi >0 = 文档「实际大小」模式 (位图按 srcDpi 渲染, 1:1 物理尺寸打印);
     *               0 = 照片「适应可打印区」模式 (等比缩放居中)
     */
    fun encodePage(bitmap: Bitmap, dpi: Int, paper: Int, srcDpi: Int = 0): ByteArray? {
        if (dpi !in SUPPORTED_DPI) return null
        val w = bitmap.width
        val h = bitmap.height
        if (w <= 0 || h <= 0) return null
        val rgba = bitmapToRgba(bitmap)
        return nativeEncodePage(rgba, w, h, dpi, paper, srcDpi)
    }

    /** 多页任务尾 (reset + UEL + PJL EOJ) */
    fun endJob(): ByteArray = nativeEndJob()
        ?: throw IllegalStateException("编码任务尾失败")
}
