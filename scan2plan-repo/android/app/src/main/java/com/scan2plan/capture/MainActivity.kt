package com.scan2plan.capture

import android.Manifest
import android.app.Activity
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.os.Bundle
import android.widget.Button
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
import com.google.ar.core.ArCoreApk
import com.google.ar.core.exceptions.UnavailableDeviceNotCompatibleException
import com.google.ar.core.exceptions.UnavailableUserDeclinedInstallationException
import java.io.File

/**
 * 采集端主界面。
 *
 * 刻意使用系统 `Activity` + `findViewById`（不用 AndroidX / Material / viewBinding）：
 * 少一层代码生成，云端 CI 第一次编译就通过的概率最高，APK 也最小。
 *
 * 使用流程：
 *   1. 首次启动会检查/安装 Google Play 服务（AR）
 *   2. 点「开始采集」，缓慢平移手机，把每个房间的墙面都扫到（每间房绕一圈）
 *   3. 点「停止」，点云写入 scan_YYYYMMDD_HHmmss/cloud.ply
 *   4. 把 cloud.ply 拷到电脑，跑 `python -m scan2plan recon cloud.ply -o out.json`
 */
class MainActivity : Activity() {

    private lateinit var previewView: ImageView
    private lateinit var tvStatus: TextView
    private lateinit var tvStat: TextView
    private lateinit var tvPath: TextView
    private lateinit var btnStart: Button
    private lateinit var btnStop: Button
    private lateinit var btnCopy: Button

    private var engine: CaptureEngine? = null
    private var writer: SessionWriter? = null
    private var lastDir: File? = null

    private var previewBitmap: Bitmap? = null
    private var previewW = 0
    private var previewH = 0

    companion object {
        private const val REQ_CAMERA = 1001
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        previewView = findViewById(R.id.preview)
        tvStatus = findViewById(R.id.tvStatus)
        tvStat = findViewById(R.id.tvStat)
        tvPath = findViewById(R.id.tvPath)
        btnStart = findViewById(R.id.btnStart)
        btnStop = findViewById(R.id.btnStop)
        btnCopy = findViewById(R.id.btnCopy)

        btnStart.setOnClickListener {
            if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
                startCapture()
            } else {
                requestPermissions(arrayOf(Manifest.permission.CAMERA), REQ_CAMERA)
            }
        }
        btnStop.setOnClickListener { stopCapture() }
        btnCopy.setOnClickListener {
            val dir = lastDir
            if (dir == null) {
                toast("还没有采集结果")
                return@setOnClickListener
            }
            val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            cm.setPrimaryClip(ClipData.newPlainText("scan2plan path", dir.absolutePath))
            toast("路径已复制")
        }

        tvPath.text = "输出目录：${outputRoot().absolutePath}"
        btnStop.isEnabled = false
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_CAMERA) {
            if (grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                startCapture()
            } else {
                toast("没有相机权限无法采集")
            }
        }
    }

    override fun onResume() {
        super.onResume()
        checkArCoreAvailability()
    }

    override fun onPause() {
        super.onPause()
        // 退到后台就停，避免相机被占用 / 采集到无效数据
        if (engine != null) stopCapture()
    }

    // ------------------------------------------------------------ ARCore

    private fun checkArCoreAvailability() {
        if (engine != null) return
        try {
            when (ArCoreApk.getInstance().checkAvailability(this)) {
                ArCoreApk.Availability.SUPPORTED_INSTALLED ->
                    tvStatus.text = "ARCore 就绪，可以开始采集"
                ArCoreApk.Availability.SUPPORTED_NOT_INSTALLED,
                ArCoreApk.Availability.SUPPORTED_APK_TOO_OLD -> {
                    tvStatus.text = "正在请求安装 Google Play 服务（AR）…"
                    ArCoreApk.getInstance().requestInstall(this, true)
                }
                ArCoreApk.Availability.UNSUPPORTED_DEVICE_NOT_CAPABLE ->
                    tvStatus.text = "这台设备不支持 ARCore，无法使用深度采集"
                else ->
                    tvStatus.text = "ARCore 状态未知（需要联网检查）"
            }
        } catch (e: UnavailableDeviceNotCompatibleException) {
            tvStatus.text = "设备不兼容 ARCore"
        } catch (e: UnavailableUserDeclinedInstallationException) {
            tvStatus.text = "用户拒绝了 ARCore 安装"
        } catch (e: Exception) {
            tvStatus.text = "ARCore 检查失败：${e.message}"
        }
    }

    private fun startCapture() {
        if (engine != null) return
        val e = CaptureEngine(this)
        engine = e

        e.onStatus = { s ->
            runOnUiThread {
                tvStatus.text = if (s.message.isBlank()) "…" else s.message
                val sb = StringBuilder()
                sb.append("点云 ").append(s.points).append(" 点")
                sb.append("   ·   深度帧 ").append(s.frames)
                if (!s.depthSupported) sb.append("   ·   ⚠ 无 Depth API")
                sb.append(if (s.tracking) "   ·   ✓ 定位正常" else "   ·   ✗ 定位中")
                tvStat.text = sb.toString()
                if (s.fatal) {
                    btnStart.isEnabled = true
                    btnStop.isEnabled = false
                    engine = null
                }
            }
        }
        e.onPreview = { bytes, w, h -> runOnUiThread { updatePreview(bytes, w, h) } }
        e.onFinished = { dir, msg ->
            runOnUiThread {
                lastDir = dir
                engine = null
                btnStart.isEnabled = true
                btnStop.isEnabled = false
                tvPath.text = "已保存：${dir.absolutePath}"
                toast(msg)
            }
        }

        val w = SessionWriter(outputRoot())
        writer = w
        btnStart.isEnabled = false
        btnStop.isEnabled = true
        tvPath.text = "输出目录：${w.sessionDir.absolutePath}"
        e.start(w)
    }

    private fun stopCapture() {
        val e = engine ?: return
        tvStatus.text = "正在保存点云…"
        e.stop()
        engine = null
    }

    private fun updatePreview(bytes: ByteArray, w: Int, h: Int) {
        if (w <= 0 || h <= 0) return
        if (previewBitmap == null || previewW != w || previewH != h) {
            previewBitmap = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
            previewW = w
            previewH = h
        }
        val bmp = previewBitmap ?: return
        val pixels = IntArray(w * h)
        var i = 0
        val n = w * h
        while (i < n && i < bytes.size) {
            val v = bytes[i].toInt() and 0xFF
            // 蓝色调：近处亮、远处暗
            pixels[i] = (0xFF shl 24) or (v shl 16) or ((v * 0.75f).toInt() shl 8) or (v / 3)
            i++
        }
        bmp.setPixels(pixels, 0, w, 0, 0, w, h)
        previewView.setImageBitmap(bmp)
    }

    private fun outputRoot(): File {
        val dir = getExternalFilesDir(null) ?: filesDir
        val root = File(dir, "scan2plan")
        if (!root.exists()) root.mkdirs()
        return root
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
}
