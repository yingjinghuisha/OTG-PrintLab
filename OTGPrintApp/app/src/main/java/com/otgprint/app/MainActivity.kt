package com.otgprint.app

import android.annotation.SuppressLint
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.hardware.usb.UsbDevice
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * OTG 直连打印 — 主界面
 *
 * 内容来源:
 *   1. 本地图库选择 (图片 / PDF / Word)
 *   2. 微信/QQ 分享 (ACTION_SEND / ACTION_SEND_MULTIPLE)
 *   3. 微信/QQ 「其他应用打开」 (ACTION_VIEW)
 *
 * 图片: 单页直接编码
 * PDF:  PdfRenderer 逐页渲染 → 多页流式编码
 * DOCX: 解析文本+格式 → Canvas 排版 → 多页流式编码
 * DOC:  老二进制格式不支持, 提示转存 docx/PDF
 */
class MainActivity : AppCompatActivity(), UsbPrinterManager.Listener {

    /** 当前打印作业 */
    private sealed class Job {
        class Image(val bitmap: Bitmap) : Job()
        class Document(
            val kind: DocumentRenderer.DocKind,
            val uri: Uri,
            val pageCount: Int
        ) : Job()
    }

    private lateinit var usb: UsbPrinterManager
    private lateinit var preview: ImageView
    private lateinit var statusText: TextView
    private lateinit var progress: ProgressBar
    private lateinit var printBtn: Button
    private lateinit var paperSpinner: Spinner
    private lateinit var dpiSpinner: Spinner
    private lateinit var saveBinBtn: Button

    private var job: Job? = null
    private var currentName: String = ""
    private var lastStream: ByteArray? = null
    private val scope = CoroutineScope(Dispatchers.Main)

    // USB 状态
    private var printerDevice: UsbDevice? = null
    private var printerOpened = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        preview = findViewById(R.id.preview)
        statusText = findViewById(R.id.status)
        progress = findViewById(R.id.progress)
        printBtn = findViewById(R.id.btn_print)
        paperSpinner = findViewById(R.id.spinner_paper)
        dpiSpinner = findViewById(R.id.spinner_dpi)
        saveBinBtn = findViewById(R.id.btn_save_bin)

        paperSpinner.adapter = ArrayAdapter(
            this, android.R.layout.simple_spinner_dropdown_item,
            listOf("A4", "Letter", "4x6照片")
        )
        dpiSpinner.adapter = ArrayAdapter(
            this, android.R.layout.simple_spinner_dropdown_item,
            listOf("600 DPI (高质量)", "300 DPI (快速)")
        )

        usb = UsbPrinterManager(this).also { it.listener = this; it.register() }

        findViewById<Button>(R.id.btn_pick).setOnClickListener { pickFromGallery() }
        printBtn.setOnClickListener { printClicked() }
        saveBinBtn.setOnClickListener { saveLastStream() }
        saveBinBtn.isEnabled = false

        // 处理唤起本应用的分享/打开 Intent
        handleShareIntent(intent)

        refreshUsbState()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleShareIntent(intent)
    }

    // ------------------------------------------------------------------
    // 内容接收: 本地选择 + 微信/QQ 分享 (图片 / PDF / Word)
    // ------------------------------------------------------------------

    private fun pickFromGallery() {
        val it = Intent(Intent.ACTION_GET_CONTENT).apply {
            type = "*/*"
            addCategory(Intent.CATEGORY_OPENABLE)
            // 图片 + PDF + Word
            putExtra(
                Intent.EXTRA_MIME_TYPES, arrayOf(
                    "image/*",
                    "application/pdf",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "application/msword"
                )
            )
        }
        startActivityForResult(Intent.createChooser(it, "选择文件"), REQ_PICK)
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQ_PICK && resultCode == RESULT_OK) {
            data?.data?.let { loadUri(it, "本地文件", data.type) }
        }
    }

    private fun handleShareIntent(intent: Intent?) {
        intent ?: return
        val mime = intent.type
        when (intent.action) {
            Intent.ACTION_SEND -> {
                @Suppress("DEPRECATION")
                val uri: Uri? = intent.getParcelableExtra(Intent.EXTRA_STREAM)
                uri?.let { loadUri(it, "分享文件", mime) }
                    ?: intent.data?.let { loadUri(it, "分享文件", mime) }
            }
            Intent.ACTION_SEND_MULTIPLE -> {
                @Suppress("DEPRECATION")
                val uris: List<Uri>? = intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM)
                uris?.firstOrNull()?.let { loadUri(it, "分享文件(多选)", mime) }
            }
            Intent.ACTION_VIEW -> {
                intent.data?.let { loadUri(it, "打开文件", intent.type ?: mime) }
            }
        }
    }

    /** 按类型分流加载 (魔数优先识别: 微信/QQ 分享的文件常无扩展名、mime 不准) */
    private fun loadUri(uri: Uri, source: String, mime: String? = null) {
        scope.launch {
            statusText.text = "正在识别文件…"
            val kind = withContext(Dispatchers.IO) {
                runCatching { DocumentRenderer.sniffKind(this@MainActivity, uri, mime) }
                    .getOrDefault(DocumentRenderer.FileKind.UNKNOWN)
            }
            when (kind) {
                DocumentRenderer.FileKind.PDF -> loadPdf(uri, source)
                DocumentRenderer.FileKind.DOCX -> loadDocx(uri, source)
                DocumentRenderer.FileKind.LEGACY_OFFICE ->
                    toast("暂不支持老版 Office 格式 (.doc/.xls/.ppt)，请在Word/WPS中另存为 .docx 或 PDF")
                else -> loadImage(uri, source)   // 图片 / 未知类型按图片尝试
            }
        }
    }

    // ---- 图片 ----

    private fun loadImage(uri: Uri, source: String) {
        scope.launch {
            try {
                val bmp = withContext(Dispatchers.IO) {
                    contentResolver.openInputStream(uri)?.use { ins ->
                        // 大图限制: 最长边 <= 3200px (避免内存爆炸, 打印600dpi足够)
                        val opts = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                        BitmapFactory.decodeStream(ins, null, opts)
                        var sample = 1
                        var maxSide = maxOf(opts.outWidth, opts.outHeight)
                        while (maxSide / sample > 3200) sample *= 2
                        contentResolver.openInputStream(uri)?.use { ins2 ->
                            BitmapFactory.decodeStream(
                                ins2, null,
                                BitmapFactory.Options().apply { inSampleSize = sample }
                            )
                        }
                    }
                }
                if (bmp != null) {
                    job = Job.Image(bmp)
                    currentName = "$source ${bmp.width}x${bmp.height}"
                    preview.setImageBitmap(bmp)
                    statusText.text = "已加载: $currentName\n等待打印…"
                    printBtn.isEnabled = true
                } else {
                    toast("图片解码失败")
                }
            } catch (e: Exception) {
                toast("读取图片失败: ${e.message}")
            }
        }
    }

    // ---- PDF ----

    private fun loadPdf(uri: Uri, source: String) {
        scope.launch {
            statusText.text = "正在打开 PDF…"
            val count = withContext(Dispatchers.IO) {
                DocumentRenderer.pdfPageCount(this@MainActivity, uri)
            }
            if (count <= 0) return@launch toast("PDF 打开失败 (文件损坏或无法访问)")
            val first = withContext(Dispatchers.IO) {
                DocumentRenderer.renderPdfPage(this@MainActivity, uri, 0)
            } ?: return@launch toast("PDF 渲染失败")
            job = Job.Document(DocumentRenderer.DocKind.Pdf, uri, count)
            currentName = "$source PDF ${count}页"
            preview.setImageBitmap(first)
            statusText.text = "已加载: $currentName\n等待打印…"
            printBtn.isEnabled = true
        }
    }

    // ---- DOCX ----

    private fun loadDocx(uri: Uri, source: String) {
        scope.launch {
            statusText.text = "正在解析 Word 文档…"
            val count = withContext(Dispatchers.IO) {
                runCatching { DocumentRenderer.docxPageCount(this@MainActivity, uri) }.getOrDefault(0)
            }
            if (count <= 0) return@launch toast("Word 解析失败 (仅支持 .docx, 不支持 .doc)")
            val first = withContext(Dispatchers.IO) {
                runCatching { DocumentRenderer.renderDocxPage(this@MainActivity, uri, 0) }.getOrNull()
            } ?: return@launch toast("Word 渲染失败")
            job = Job.Document(DocumentRenderer.DocKind.Docx, uri, count)
            currentName = "$source Word ${count}页"
            preview.setImageBitmap(first)
            statusText.text = "已加载: $currentName\n等待打印…"
            printBtn.isEnabled = true
        }
    }

    // ------------------------------------------------------------------
    // USB 状态
    // ------------------------------------------------------------------

    private fun refreshUsbState() {
        printerDevice = usb.findPrinter()
        if (printerDevice == null) {
            statusText.text = (statusText.text.toString() + "\n[USB] 未检测到HP打印机 (OTG连接后自动识别)")
            printBtn.isEnabled = job != null
            return
        }
        if (usb.hasPermission(printerDevice!!)) {
            printerOpened = usb.open(printerDevice!!)
            updateUsbStatusLine()
        } else {
            usb.requestPermissionIfNeeded(printerDevice!!)
        }
    }

    private fun updateUsbStatusLine() {
        val dev = printerDevice
        val line = if (dev == null) {
            "[USB] 未检测到打印机"
        } else if (printerOpened) {
            "[USB] ${dev.productName ?: "HP打印机"} 已连接就绪"
        } else {
            "[USB] ${dev.productName ?: "HP打印机"} 连接失败"
        }
        val base = statusText.text.toString().substringBefore("\n[USB]")
        statusText.text = if (job != null) "$base\n$line" else line
    }

    override fun onDeviceAttached(device: UsbDevice) {
        runOnUiThread {
            printerDevice = device
            usb.requestPermissionIfNeeded(device)
        }
    }

    override fun onDeviceDetached() {
        runOnUiThread {
            printerOpened = false
            printerDevice = null
            updateUsbStatusLine()
            toast("打印机已断开")
        }
    }

    override fun onPermissionGranted(device: UsbDevice) {
        runOnUiThread {
            printerDevice = device
            printerOpened = usb.open(device)
            updateUsbStatusLine()
            if (printerOpened) toast("打印机已就绪")
        }
    }

    override fun onPermissionDenied() {
        runOnUiThread { toast("未授予USB权限, 无法打印") }
    }

    // ------------------------------------------------------------------
    // 打印
    // ------------------------------------------------------------------

    private fun printClicked() {
        val currentJob = job ?: return toast("请先选择要打印的内容")
        if (!printerOpened) {
            refreshUsbState()
            if (!printerOpened) return toast("打印机未连接")
        }
        val dpi = if (dpiSpinner.selectedItemPosition == 1) 300 else 600
        val paper = paperSpinner.selectedItemPosition

        printBtn.isEnabled = false
        progress.isIndeterminate = true
        progress.visibility = android.view.View.VISIBLE
        statusText.text = "编码中 (${dpi}DPI ${paperSpinner.selectedItem})…"

        scope.launch {
            when (currentJob) {
                is Job.Image -> {
                    // ---- 图片: 单页完整流 ----
                    val stream = withContext(Dispatchers.Default) {
                        runCatching {
                            PrintEngine.encode(currentJob.bitmap, dpi, paper)
                        }.getOrNull()
                    }
                    if (stream == null) {
                        statusText.text = "编码失败"
                        progress.visibility = android.view.View.GONE
                        printBtn.isEnabled = true
                        return@launch
                    }
                    lastStream = stream
                    saveBinBtn.isEnabled = true
                    statusText.text = "编码完成 ${stream.size / 1024}KB, 发送中…"
                    progress.isIndeterminate = false
                    progress.max = stream.size
                    progress.progress = 0
                    val ok = withContext(Dispatchers.IO) { usb.sendStream(stream) }
                    if (ok) {
                        statusText.text = "打印完成 ✓ (${stream.size / 1024}KB 已发送)"
                    }
                    progress.visibility = android.view.View.GONE
                    printBtn.isEnabled = true
                }

                is Job.Document -> {
                    // ---- PDF/Word: 多页流式编码 ----
                    val total = currentJob.pageCount
                    statusText.text = "编码中 第1/$total 页…"
                    val chunks = withContext(Dispatchers.Default) {
                        val parts = mutableListOf<ByteArray>()
                        parts.add(PrintEngine.beginJob())
                        var failed = false
                        for (i in 0 until total) {
                            // 渲染一页 → 编码一页 → 立即释放位图 (内存峰值=单页)
                            val bmp = withContext(Dispatchers.IO) {
                                when (currentJob.kind) {
                                    is DocumentRenderer.DocKind.Pdf ->
                                        DocumentRenderer.renderPdfPage(this@MainActivity, currentJob.uri, i)
                                    is DocumentRenderer.DocKind.Docx ->
                                        runCatching {
                                            DocumentRenderer.renderDocxPage(this@MainActivity, currentJob.uri, i)
                                        }.getOrNull()
                                }
                            }
                            if (bmp == null) {
                                failed = true
                                break
                            }
                            // 文档按实际大小打印 (位图按 RENDER_DPI 渲染, 1:1 物理尺寸)
                            val part = PrintEngine.encodePage(
                                bmp, dpi, paper, DocumentRenderer.RENDER_DPI
                            )
                            bmp.recycle()
                            if (part == null) {
                                failed = true
                                break
                            }
                            parts.add(part)
                            withContext(Dispatchers.Main) {
                                statusText.text = "编码中 第${i + 1}/$total 页…"
                            }
                        }
                        if (!failed) parts.add(PrintEngine.endJob())
                        if (failed) null else parts
                    }
                    if (chunks == null) {
                        statusText.text = "编码失败 (页面渲染异常)"
                        progress.visibility = android.view.View.GONE
                        printBtn.isEnabled = true
                        return@launch
                    }
                    val totalBytes = chunks.sumOf { it.size }
                    statusText.text = "编码完成 $total 页 ${totalBytes / 1024}KB, 发送中…"
                    progress.isIndeterminate = false
                    progress.max = totalBytes.toInt()
                    progress.progress = 0

                    // 拼接保存调试流 (可选), 分块直发打印机
                    val ok = withContext(Dispatchers.IO) { usb.sendChunks(chunks) }
                    if (ok) {
                        lastStream = ByteArray(totalBytes).also { buf ->
                            var pos = 0
                            for (c in chunks) {
                                System.arraycopy(c, 0, buf, pos, c.size)
                                pos += c.size
                            }
                        }
                        saveBinBtn.isEnabled = true
                        statusText.text = "打印完成 ✓ ($total 页 ${totalBytes / 1024}KB)"
                    }
                    progress.visibility = android.view.View.GONE
                    printBtn.isEnabled = true
                }
            }
        }
    }

    override fun onPrintProgress(sent: Long, total: Long) {
        runOnUiThread {
            progress.max = total.toInt()
            progress.progress = sent.toInt()
            statusText.text = "发送中 ${sent * 100 / total}% (${sent / 1024}/${total / 1024}KB)"
        }
    }

    override fun onPrintDone() {}

    override fun onPrintError(msg: String) {
        runOnUiThread {
            statusText.text = "打印失败: $msg"
            progress.visibility = android.view.View.GONE
            printBtn.isEnabled = true
        }
    }

    // ------------------------------------------------------------------
    // 调试: 保存打印流 bin (导出与黄金样本二进制比对)
    // ------------------------------------------------------------------

    @SuppressLint("ObsoleteSdkInt")
    private fun saveLastStream() {
        val stream = lastStream ?: return
        scope.launch {
            try {
                val dir = File(
                    Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOCUMENTS),
                    "OTGPrint"
                )
                if (!dir.exists()) dir.mkdirs()
                val name = "android_output_" +
                        SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date()) + ".bin"
                val f = File(dir, name)
                withContext(Dispatchers.IO) { f.writeBytes(stream) }
                toast("已保存: Documents/OTGPrint/$name")
            } catch (e: Exception) {
                // 公共目录写入失败时退回应用私有目录
                try {
                    val f = File(filesDir, "android_output.bin")
                    f.writeBytes(stream)
                    toast("已保存(私有): ${f.absolutePath}")
                } catch (e2: Exception) {
                    toast("保存失败: ${e2.message}")
                }
            }
        }
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()

    override fun onDestroy() {
        usb.unregister()
        usb.close()
        super.onDestroy()
    }

    companion object {
        private const val REQ_PICK = 1001
    }
}
