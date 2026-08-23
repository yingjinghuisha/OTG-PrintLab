package com.otgprint.app

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.pdf.PdfRenderer
import android.net.Uri
import android.util.Xml
import org.xmlpull.v1.XmlPullParser
import java.io.InputStream
import java.util.zip.ZipInputStream

/**
 * 文档渲染器: PDF / DOCX → 逐页 Bitmap
 *
 * HP 2132 是主机渲染打印机 (不解析文档), 由 APP 把文档渲染成位图
 * 后走 PCL3-GUI 编码链路。
 *
 * - PDF: 系统 PdfRenderer, 按 300dpi 渲染 (600dpi A4 位图 139MB 易 OOM;
 *   文本 300dpi 渲染 + 编码器放大到 600dpi 打印, 质量已足够)
 * - DOCX: ZIP 解包 word/document.xml, XmlPullParser 提取段落文本与
 *   基本格式 (粗体/斜体/字号/颜色), Canvas 排版绘制 (自动换行/分页)
 * - DOC (老二进制格式) 不支持: 提示用户转存 docx/PDF
 */
object DocumentRenderer {

    /** 文档类型上限渲染 DPI (A4@300 = 2480x3508 ≈ 35MB/页, 内存安全) */
    const val RENDER_DPI = 300

    // A4 纵向, 单位: 点 (1pt = 1/72 inch)
    private const val PAGE_W_PT = 595f
    private const val PAGE_H_PT = 842f

    sealed class DocKind { object Pdf : DocKind(); object Docx : DocKind() }

    // ------------------------------------------------------------------
    // 文件类型识别 (魔数优先: 微信/QQ 分享常无扩展名且 mime 为 octet-stream)
    // ------------------------------------------------------------------

    enum class FileKind { PDF, DOCX, LEGACY_OFFICE, IMAGE, UNKNOWN }

    /**
     * 按文件头魔数识别类型 (不可靠的 mime/扩展名只作兜底):
     * %PDF -> PDF; PK.. -> OOXML (含 word/document.xml 才是 docx);
     * D0CF11E0 -> 老版 Office 二进制 (.doc/.xls/.ppt)
     */
    fun sniffKind(context: Context, uri: Uri, mime: String?): FileKind {
        try {
            val head = ByteArray(8)
            val n = context.contentResolver.openInputStream(uri)?.use { it.read(head) } ?: 0
            if (n >= 4) {
                if (head[0] == '%'.code.toByte() && head[1] == 'P'.code.toByte() &&
                    head[2] == 'D'.code.toByte() && head[3] == 'F'.code.toByte()
                ) return FileKind.PDF
                if (head[0] == 0x50.toByte() && head[1] == 0x4B.toByte()) {
                    return if (zipHasEntry(context, uri, "word/document.xml"))
                        FileKind.DOCX else FileKind.UNKNOWN
                }
                if (head[0] == 0xD0.toByte() && head[1] == 0xCF.toByte() &&
                    head[2] == 0x11.toByte() && head[3] == 0xE0.toByte()
                ) return FileKind.LEGACY_OFFICE
            }
        } catch (_: Exception) {
        }
        // 兜底: mime / 扩展名
        if (isPdf(mime, uri)) return FileKind.PDF
        if (isDocx(mime, uri)) return FileKind.DOCX
        if (isLegacyDoc(mime, uri)) return FileKind.LEGACY_OFFICE
        if (mime?.startsWith("image/") == true) return FileKind.IMAGE
        return FileKind.UNKNOWN
    }

    private fun zipHasEntry(context: Context, uri: Uri, name: String): Boolean {
        return try {
            context.contentResolver.openInputStream(uri)?.use { ins ->
                ZipInputStream(ins.buffered()).use { zis ->
                    var e = zis.nextEntry
                    while (e != null) {
                        if (e.name == name) return true
                        e = zis.nextEntry
                    }
                    false
                }
            } ?: false
        } catch (_: Exception) {
            false
        }
    }

    // ------------------------------------------------------------------
    // PDF: PdfRenderer 逐页渲染
    // ------------------------------------------------------------------

    /** PDF 页数; 打开失败返回 0 */
    fun pdfPageCount(context: Context, uri: Uri): Int {
        return try {
            context.contentResolver.openFileDescriptor(uri, "r")?.use { pfd ->
                PdfRenderer(pfd).use { it.pageCount }
            } ?: 0
        } catch (e: Exception) {
            0
        }
    }

    /**
     * 渲染 PDF 单页。横向页自动旋转 90° 转成纵向 (纸张按纵向进纸)。
     * @param pageIndex 从 0 开始
     * @return 白底位图; 页码越界/失败返回 null
     */
    fun renderPdfPage(context: Context, uri: Uri, pageIndex: Int): Bitmap? {
        return try {
            context.contentResolver.openFileDescriptor(uri, "r")?.use { pfd ->
                PdfRenderer(pfd).use { renderer ->
                    if (pageIndex < 0 || pageIndex >= renderer.pageCount) return@use null
                    renderer.openPage(pageIndex).use { page ->
                        // PDF 页面尺寸单位: 点(1/72"), 换算到渲染像素
                        val w = (page.width * RENDER_DPI / 72f).toInt().coerceAtLeast(1)
                        val h = (page.height * RENDER_DPI / 72f).toInt().coerceAtLeast(1)
                        val bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
                        bmp.eraseColor(Color.WHITE)
                        page.render(bmp, null, null, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY)
                        if (w > h) {
                            // 横向页 → 纵向 (保持渲染 DPI, 实际大小打印不受影响)
                            val m = Matrix().apply { postRotate(90f) }
                            val rotated = Bitmap.createBitmap(bmp, 0, 0, w, h, m, true)
                            bmp.recycle()
                            rotated
                        } else {
                            bmp
                        }
                    }
                }
            }
        } catch (e: Exception) {
            null
        }
    }

    fun isPdf(mime: String?, uri: Uri): Boolean {
        if (mime == "application/pdf") return true
        val path = uri.lastPathSegment?.lowercase() ?: return false
        return path.endsWith(".pdf")
    }

    fun isDocx(mime: String?, uri: Uri): Boolean {
        if (mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document") return true
        val path = uri.lastPathSegment?.lowercase() ?: return false
        return path.endsWith(".docx")
    }

    fun isLegacyDoc(mime: String?, uri: Uri): Boolean {
        if (mime == "application/msword") return true
        val path = uri.lastPathSegment?.lowercase() ?: return false
        return path.endsWith(".doc")
    }

    // ------------------------------------------------------------------
    // DOCX: 解析 + Canvas 排版
    // ------------------------------------------------------------------

    /** 一段文本 (一个 w:p) */
    private data class Para(
        val text: String,
        val bold: Boolean = false,
        val italic: Boolean = false,
        val sizePt: Float = 11f,        // Word 默认 11pt (Calibri)
        val color: Int = Color.BLACK,
        val pageBreakAfter: Boolean = false
    )

    /** 解析 docx → 段落列表; 解析失败返回 null */
    private fun parseDocx(context: Context, uri: Uri): List<Para>? {
        return try {
            context.contentResolver.openInputStream(uri)?.use { ins ->
                parseDocxStream(ins)
            }
        } catch (e: Exception) {
            null
        }
    }

    private fun parseDocxStream(ins: InputStream): List<Para> {
        val paras = mutableListOf<Para>()
        ZipInputStream(ins.buffered()).use { zis ->
            var entry = zis.nextEntry
            while (entry != null) {
                if (entry.name == "word/document.xml") {
                    parseDocumentXml(zis, paras)
                    break
                }
                entry = zis.nextEntry
            }
        }
        if (paras.isEmpty()) paras.add(Para("（空文档）"))
        return paras
    }

    private fun parseDocumentXml(ins: InputStream, out: MutableList<Para>) {
        val parser = Xml.newPullParser()
        // 关键: 必须开启命名空间处理, parser.name 才返回本地名 ("p"/"t"/"b"),
        // 关闭时返回带前缀的 "w:p"/"w:t" 导致所有分支失配、解析结果恒为空!
        parser.setFeature(XmlPullParser.FEATURE_PROCESS_NAMESPACES, true)
        parser.setInput(ins, null)

        var bold = false
        var italic = false
        var sizePt = 11f
        var color = Color.BLACK
        var pendingPageBreak = false
        var inPPr = false   // w:pPr (段落属性) 内的 rPr 是段落标记格式, 不应用到文字
        val text = StringBuilder()

        var event = parser.eventType
        while (event != XmlPullParser.END_DOCUMENT) {
            when (event) {
                XmlPullParser.START_TAG -> when (parser.name) {
                    "p" -> { text.setLength(0); bold = false; italic = false; sizePt = 11f; color = Color.BLACK }
                    "pPr" -> inPPr = true
                    "r" -> { /* run 开始, 继承段落默认 */ }
                    "b" -> if (!inPPr) bold = parser.getAttributeValue(null, "val") != "0"
                    "i" -> if (!inPPr) italic = parser.getAttributeValue(null, "val") != "0"
                    "sz" -> if (!inPPr) {
                        parser.getAttributeValue(null, "val")?.toFloatOrNull()?.let { sizePt = it / 2f }
                    }
                    "color" -> if (!inPPr) {
                        parser.getAttributeValue(null, "val")?.let {
                            if (it.length == 6) runCatching { color = Color.parseColor("#$it") }
                        }
                    }
                    "t" -> text.append(parser.nextText())
                    "br" -> {
                        val type = parser.getAttributeValue(null, "type")
                        if (type == "page") pendingPageBreak = true
                        else text.append('\n')
                    }
                    "lastRenderedPageBreak" -> pendingPageBreak = true
                    "tab" -> text.append("    ")
                }
                XmlPullParser.END_TAG -> when (parser.name) {
                    "pPr" -> inPPr = false
                    "p" -> {
                        // 表格单元格文本也会到这里 (w:tc 内的 w:p), 统一按段落处理
                        out.add(Para(text.toString(), bold, italic, sizePt, color, pendingPageBreak))
                        pendingPageBreak = false
                    }
                    // 表格: 单元格间加 " | " 分隔, 避免各单元格文字连在一起
                    "tc" -> {
                        val last = out.lastOrNull()
                        if (last != null && last.text.isNotEmpty() && !last.text.endsWith(" | ")) {
                            out[out.size - 1] = last.copy(text = last.text + " | ")
                        }
                    }
                }
            }
            event = parser.next()
        }
    }

    /**
     * 渲染 DOCX 单页。
     * 解析全文 → 排版分页 → 返回第 pageIndex 页位图 (从 0 开始)。
     * 每次调用都重新解析 (简单但够用: 页数少; 打印时逐页调用, 内存峰值一页)
     */
    fun renderDocxPage(context: Context, uri: Uri, pageIndex: Int): Bitmap? {
        val paras = parseDocx(context, uri) ?: return null
        return renderDocxPage(paras, pageIndex)
    }

    fun docxPageCount(context: Context, uri: Uri): Int {
        val paras = parseDocx(context, uri) ?: return 0
        return countDocxPages(paras)
    }

    // ---- 排版引擎 ----
    // 布局: A4 白底, 1 inch 页边距, 段落间 0.6 行距, 自动换行

    private fun ptToPx(pt: Float): Int = (pt * RENDER_DPI / 72f).toInt()

    private class Layouter(val paras: List<Para>) {
        val pageW = ptToPx(PAGE_W_PT)
        val pageH = ptToPx(PAGE_H_PT)
        val margin = RENDER_DPI // 1 inch
        val contentW = pageW - 2 * margin
        val contentH = pageH - 2 * margin
        val lineSpacing = 1.4f

        /** 预排版: 每页的行列表 */
        fun layout(): List<List<Line>> {
            val pages = mutableListOf<MutableList<Line>>()
            var curPage = mutableListOf<Line>()
            var y = 0f

            fun newPage() {
                pages.add(curPage)
                curPage = mutableListOf()
                y = 0f
            }

            for (para in paras) {
                val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
                    textSize = ptToPx(para.sizePt).toFloat()
                    isFakeBoldText = para.bold
                    textSkewX = if (para.italic) -0.25f else 0f
                    color = para.color
                }
                val fontH = paint.fontSpacing
                val step = fontH * lineSpacing

                if (para.text.isBlank()) {
                    // 空段落 = 空行
                    y += step
                    if (y > contentH) { newPage(); y = step }
                    continue
                }

                // 按显式换行拆分, 再逐段自动换行
                val lines = mutableListOf<String>()
                for (raw in para.text.split('\n')) {
                    if (raw.isEmpty()) { lines.add(""); continue }
                    var rest = raw
                    while (true) {
                        if (paint.measureText(rest) <= contentW) { lines.add(rest); break }
                        val n = paint.breakText(rest, true, contentW.toFloat(), null)
                        if (n <= 0) { lines.add(rest); break }
                        lines.add(rest.substring(0, n))
                        rest = rest.substring(n)
                    }
                }

                for (line in lines) {
                    if (y + step > contentH) newPage()
                    curPage.add(Line(line, paint, y))
                    y += step
                }
                // 段后间距
                y += step * 0.3f

                // 显式分页符
                if (para.pageBreakAfter) {
                    newPage()
                    y = 0f
                }
            }
            pages.add(curPage)
            // 文档末尾的分页符会产生空白尾页, 去掉
            if (pages.size > 1 && curPage.isEmpty()) pages.removeAt(pages.size - 1)
            return pages
        }
    }

    private class Line(val text: String, val paint: Paint, val y: Float)

    private fun countDocxPages(paras: List<Para>): Int =
        Layouter(paras).layout().size

    private fun renderDocxPage(paras: List<Para>, pageIndex: Int): Bitmap? {
        val lay = Layouter(paras)
        val pages = lay.layout()
        if (pageIndex < 0 || pageIndex >= pages.size) return null
        val bmp = Bitmap.createBitmap(lay.pageW, lay.pageH, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bmp)
        canvas.drawColor(Color.WHITE)

        for (line in pages[pageIndex]) {
            // 基线 = 行顶 + 字体行高*0.8 (近似 ascent 对齐)
            val baseline = lay.margin + line.y + line.paint.fontSpacing * 0.8f
            canvas.drawText(line.text, lay.margin.toFloat(), baseline, line.paint)
        }
        return bmp
    }
}
