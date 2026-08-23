package com.otgprint.app

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbEndpoint
import android.hardware.usb.UsbInterface
import android.hardware.usb.UsbManager
import android.os.Build
import android.util.Log

/**
 * USB-OTG 打印机管理
 *
 * 职责: 枚举 HP 打印机(VID=0x03F0) / 申请权限 / 打开设备 /
 *       定位 BULK-OUT 端点 / 分片 bulkTransfer 下发打印流
 */
class UsbPrinterManager(private val context: Context) {

    companion object {
        private const val TAG = "UsbPrinterManager"
        const val HP_VID = 0x03F0
        const val DJ2132_PID = 0xE111  // 抓包实测 (文档标注 0x9111 不准确)

        /** 单次 bulkTransfer 分片大小 (Android 建议 <= 16384) */
        private const val CHUNK = 16 * 1024
        private const val ACTION_USB_PERMISSION = "com.otgprint.app.USB_PERMISSION"
    }

    interface Listener {
        fun onDeviceAttached(device: UsbDevice) {}
        fun onDeviceDetached() {}
        fun onPermissionGranted(device: UsbDevice) {}
        fun onPermissionDenied() {}
        fun onPrintProgress(sent: Long, total: Long) {}
        fun onPrintDone() {}
        fun onPrintError(msg: String) {}
    }

    var listener: Listener? = null

    private val usbManager: UsbManager
        get() = context.getSystemService(Context.USB_SERVICE) as UsbManager

    private var connection: UsbDeviceConnection? = null
    private var printerInterface: UsbInterface? = null
    private var bulkOutEndpoint: UsbEndpoint? = null
    private var currentDevice: UsbDevice? = null

    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(ctx: Context, intent: Intent) {
            when (intent.action) {
                UsbManager.ACTION_USB_DEVICE_ATTACHED -> {
                    val dev = intent.getParcelableExtra<UsbDevice>(UsbManager.EXTRA_DEVICE)
                    dev?.let {
                        listener?.onDeviceAttached(it)
                        requestPermissionIfNeeded(it)
                    }
                }
                UsbManager.ACTION_USB_DEVICE_DETACHED -> {
                    val dev = intent.getParcelableExtra<UsbDevice>(UsbManager.EXTRA_DEVICE)
                    if (dev == currentDevice) {
                        close()
                        listener?.onDeviceDetached()
                    }
                }
                ACTION_USB_PERMISSION -> {
                    val granted = intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false)
                    val dev = intent.getParcelableExtra<UsbDevice>(UsbManager.EXTRA_DEVICE)
                    if (granted && dev != null) {
                        listener?.onPermissionGranted(dev)
                    } else {
                        listener?.onPermissionDenied()
                    }
                }
            }
        }
    }

    fun register() {
        val filter = IntentFilter().apply {
            addAction(UsbManager.ACTION_USB_DEVICE_ATTACHED)
            addAction(UsbManager.ACTION_USB_DEVICE_DETACHED)
            addAction(ACTION_USB_PERMISSION)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            context.registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            context.registerReceiver(receiver, filter)
        }
    }

    fun unregister() {
        try {
            context.unregisterReceiver(receiver)
        } catch (_: Exception) {
        }
    }

    /** 枚举当前已连接的 HP 打印机 (优先精确匹配 DJ2132) */
    fun findPrinter(): UsbDevice? {
        val devices = usbManager.deviceList.values
        return devices.firstOrNull { it.vendorId == HP_VID && it.productId == DJ2132_PID }
            ?: devices.firstOrNull { it.vendorId == HP_VID }
    }

    fun hasPermission(device: UsbDevice): Boolean = usbManager.hasPermission(device)

    fun requestPermissionIfNeeded(device: UsbDevice) {
        if (!usbManager.hasPermission(device)) {
            val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                PendingIntent.FLAG_MUTABLE
            } else {
                0
            }
            val pi = PendingIntent.getBroadcast(
                context, 0,
                Intent(ACTION_USB_PERMISSION).setPackage(context.packageName),
                flags
            )
            usbManager.requestPermission(device, pi)
        } else {
            listener?.onPermissionGranted(device)
        }
    }

    /**
     * 打开打印机并定位 BULK-OUT 端点。
     * 打印类设备: interface class=7(Printer), 备选: 第一个含 BULK-OUT 的接口。
     */
    fun open(device: UsbDevice): Boolean {
        if (usbManager.hasPermission(device).not()) return false
        val conn = usbManager.openDevice(device) ?: run {
            Log.e(TAG, "openDevice 失败")
            return false
        }

        var iface: UsbInterface? = null
        var ep: UsbEndpoint? = null

        // 首选: 打印类接口 (class 0x07)
        for (i in 0 until device.interfaceCount) {
            val itf = device.getInterface(i)
            if (itf.interfaceClass == UsbConstants.USB_CLASS_PRINTER) {
                for (j in 0 until itf.endpointCount) {
                    val e = itf.getEndpoint(j)
                    if (e.type == UsbConstants.USB_ENDPOINT_XFER_BULK &&
                        e.direction == UsbConstants.USB_DIR_OUT
                    ) {
                        iface = itf; ep = e; break
                    }
                }
                if (ep != null) break
            }
        }
        // 备选: 任意含 BULK-OUT 的接口
        if (ep == null) {
            outer@ for (i in 0 until device.interfaceCount) {
                val itf = device.getInterface(i)
                for (j in 0 until itf.endpointCount) {
                    val e = itf.getEndpoint(j)
                    if (e.type == UsbConstants.USB_ENDPOINT_XFER_BULK &&
                        e.direction == UsbConstants.USB_DIR_OUT
                    ) {
                        iface = itf; ep = e
                        break@outer
                    }
                }
            }
        }

        if (iface == null || ep == null) {
            Log.e(TAG, "未找到 BULK-OUT 端点")
            conn.close()
            return false
        }

        if (!conn.claimInterface(iface, true)) {
            Log.e(TAG, "claimInterface 失败")
            conn.close()
            return false
        }

        connection = conn
        printerInterface = iface
        bulkOutEndpoint = ep
        currentDevice = device
        Log.i(TAG, "打印机已打开: ${device.deviceName} ep=${ep.address}")
        return true
    }

    val isConnected: Boolean get() = connection != null && bulkOutEndpoint != null

    /**
     * 下发完整打印流 (分片 bulkTransfer)。
     * 在后台线程调用。
     */
    fun sendStream(data: ByteArray): Boolean = sendChunks(listOf(data))

    /**
     * 分块序列下发 (多页打印流: 任务头 + 各页 + 任务尾, 无需拼接成大数组)。
     * 在后台线程调用。
     */
    fun sendChunks(chunks: List<ByteArray>): Boolean {
        val conn = connection ?: return false
        val ep = bulkOutEndpoint ?: return false

        val total = chunks.sumOf { it.size }.toLong()
        var sentTotal = 0L

        for (data in chunks) {
            var offset = 0
            val len = data.size
            while (offset < len) {
                val n = minOf(CHUNK, len - offset)
                val sent = conn.bulkTransfer(ep, data, offset, n, 15000)
                if (sent < 0) {
                    Log.e(TAG, "bulkTransfer 失败 @${sentTotal + offset} (len=$n)")
                    listener?.onPrintError("USB发送失败 (偏移 ${sentTotal + offset})")
                    return false
                }
                offset += sent
                sentTotal += sent
                listener?.onPrintProgress(sentTotal, total)
                // 某些打印机忙时返回短包; 防御性休眠避免占满总线
                if (sent < n) {
                    try {
                        Thread.sleep(5)
                    } catch (_: InterruptedException) {
                        Thread.currentThread().interrupt()
                        return false
                    }
                }
            }
        }
        listener?.onPrintDone()
        return true
    }

    fun close() {
        try {
            printerInterface?.let { connection?.releaseInterface(it) }
        } catch (_: Exception) {
        }
        try {
            connection?.close()
        } catch (_: Exception) {
        }
        connection = null
        printerInterface = null
        bulkOutEndpoint = null
        currentDevice = null
    }
}
