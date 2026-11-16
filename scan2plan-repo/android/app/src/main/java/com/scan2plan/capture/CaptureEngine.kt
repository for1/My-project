package com.scan2plan.capture

import android.content.Context
import android.os.Handler
import android.os.Looper
import com.google.ar.core.ArCoreApk
import com.google.ar.core.Config
import com.google.ar.core.Session
import com.google.ar.core.TrackingState
import com.google.ar.core.exceptions.CameraNotAvailableException
import com.google.ar.core.exceptions.UnavailableApkTooOldException
import com.google.ar.core.exceptions.UnavailableArcoreNotInstalledException
import com.google.ar.core.exceptions.UnavailableDeviceNotCompatibleException
import com.google.ar.core.exceptions.UnavailableSdkTooOldException
import java.io.File

/**
 * 采集引擎：独占一个后台线程跑 ARCore Session（ARCore 的 Session 是线程绑定的，
 * 必须创建/更新/关闭在同一线程，否则会出现难以复现的崩溃）。
 *
 * 采集策略：
 *   * UpdateMode.LATEST_CAMERA_IMAGE —— 只处理最新帧，宁可丢帧也不排队，避免延迟累积
 *   * DepthMode.AUTOMATIC —— 支持深度就用稠密深度，不支持则退化（本 App 会提示不可用）
 *   * 每 sampleIntervalMs 采一帧深度（默认 180ms ≈ 5.5Hz），配合 3cm 体素去重，
 *     正常 30~60 秒能扫完一套 80m² 的房子，点云约 20~60 万点
 */
class CaptureEngine(private val context: Context) {

    data class Status(
        val running: Boolean = false,
        val tracking: Boolean = false,
        val points: Int = 0,
        val frames: Int = 0,
        val trackingQuality: Int = 0,
        val message: String = "",
        val fatal: Boolean = false,
        val depthSupported: Boolean = true,
        val coverageRatio: Float = 0f,
    )

    var onStatus: ((Status) -> Unit)? = null
    var onPreview: ((ByteArray, Int, Int) -> Unit)? = null
    var onFinished: ((File, String) -> Unit)? = null

    private val main = Handler(Looper.getMainLooper())
    private var thread: Thread? = null

    @Volatile private var stopRequested = false
    @Volatile private var running = false

    var writer: SessionWriter? = null
        private set

    val accumulator = VoxelAccumulator(0.03f)
    private val stats = DepthSampler.SampleStats()

    var sampleIntervalMs: Long = 180L
    var pixelStride: Int = 2
    var minConfidence: Int = 1
    var maxDepthM: Float = 4.5f

    private fun post(s: Status) {
        main.post { onStatus?.invoke(s) }
    }

    fun start(writer: SessionWriter) {
        if (running) return
        this.writer = writer
        accumulator.clear()
        stopRequested = false
        running = true
        thread = Thread({ runLoop(writer) }, "scan2plan-capture").also {
            it.priority = Thread.NORM_PRIORITY + 1
            it.start()
        }
    }

    fun stop() {
        stopRequested = true
        thread?.let { t ->
            try {
                t.join(4000)
            } catch (_: InterruptedException) {
            }
        }
        thread = null
        running = false
    }

    private fun runLoop(writer: SessionWriter) {
        var session: Session? = null
        try {
            // 1) 可用性检查
            val availability = ArCoreApk.getInstance().checkAvailability(context)
            if (availability.isTransient) {
                Thread.sleep(300)
            }
            when (availability) {
                ArCoreApk.Availability.UNSUPPORTED_DEVICE_NOT_CAPABLE ->
                    return fail("这台设备不支持 ARCore（需要支持 AR 的机型）")
                ArCoreApk.Availability.SUPPORTED_NOT_INSTALLED,
                ArCoreApk.Availability.SUPPORTED_APK_TOO_OLD -> {
                    // 真机上这里应该走 requestInstall 流程；为保持代码简单，直接提示
                    return fail("请先安装/更新「Google Play 服务（AR）」后再试")
                }
                else -> Unit
            }

            // 2) 创建并配置 Session（全程在本线程内）
            val s = try {
                Session(context)
            } catch (e: Exception) {
                return fail("创建 ARCore 会话失败：${e.message}")
            }
            session = s

            val depthSupported = s.isDepthModeSupported(Config.DepthMode.AUTOMATIC)
            val cfg = Config(s).apply {
                depthMode = if (depthSupported) Config.DepthMode.AUTOMATIC else Config.DepthMode.DISABLED
                updateMode = Config.UpdateMode.LATEST_CAMERA_IMAGE
                planeFindingMode = Config.PlaneFindingMode.HORIZONTAL_AND_VERTICAL
                focusMode = Config.FocusMode.AUTO
                lightEstimationMode = Config.LightEstimationMode.ENVIRONMENTAL_HDR
            }
            s.configure(cfg)
            post(Status(running = true, depthSupported = depthSupported, message = if (depthSupported) "初始化完成" else "本机不支持 Depth API，只能得到稀疏点"))

            // 3) 主循环
            try {
                s.resume()
            } catch (e: CameraNotAvailableException) {
                return fail("相机不可用：${e.message}")
            }

            var lastSample = 0L
            var lastPreview = 0L
            var lastReport = 0L
            val poseMat = FloatArray(16)

            while (!stopRequested) {
                val t0 = System.nanoTime()
                val frame = try {
                    s.update()
                } catch (e: Exception) {
                    Thread.sleep(10)
                    continue
                }
                val camera = frame.camera
                val tracking = camera.trackingState == TrackingState.TRACKING

                if (tracking) {
                    camera.pose.toMatrix(poseMat, 0)
                    writer.recordPose(frame.timestamp, poseMat, camera.trackingState.name)
                }

                val now = System.currentTimeMillis()
                if (tracking && depthSupported && now - lastSample >= sampleIntervalMs) {
                    lastSample = now
                    try {
                        DepthSampler.sampleInto(
                            frame, s, accumulator,
                            stride = pixelStride,
                            maxDepthM = maxDepthM,
                            minConfidence = minConfidence,
                            stats = stats,
                        )
                    } catch (e: Exception) {
                        // 单帧失败不影响整体采集
                    }
                }

                if (depthSupported && now - lastPreview >= 260) {
                    lastPreview = now
                    try {
                        val bytes = DepthSampler.depthToPreview(frame)
                        if (bytes != null) {
                            val w = stats.depthWidth
                            val h = stats.depthHeight
                            main.post { onPreview?.invoke(bytes, w, h) }
                        }
                    } catch (e: Exception) {
                    }
                }

                if (now - lastReport >= 250) {
                    lastReport = now
                    post(
                        Status(
                            running = true,
                            tracking = tracking,
                            points = accumulator.count,
                            frames = stats.frames,
                            trackingQuality = 0,
                            message = if (tracking) "正在采集" else "请对准房间、缓慢移动让设备重新定位",
                            depthSupported = depthSupported,
                            coverageRatio = (accumulator.approxArea() / 300f).coerceIn(0f, 1f),
                        )
                    )
                }

                val dt = (System.nanoTime() - t0) / 1_000_000
                if (dt < 8) {
                    try {
                        Thread.sleep(8 - dt)
                    } catch (_: InterruptedException) {
                    }
                }
            }

            s.pause()
        } catch (t: Throwable) {
            fail("采集异常：${t.message}")
        } finally {
            try {
                session?.close()
            } catch (_: Exception) {
            }
            // 落盘
            try {
                writer.flushPoses()
                val bytes = writer.writeCloud(accumulator)
                writer.writeMeta(
                    accumulator, stats,
                    if (stats.frames > 0) "AUTOMATIC" else "DISABLED",
                    mapOf("ply_bytes" to bytes),
                )
                val msg = "已保存 ${accumulator.count} 点（${stats.frames} 帧深度）"
                main.post { onFinished?.invoke(writer.sessionDir, msg) }
            } catch (e: Exception) {
                main.post { onStatus?.invoke(Status(message = "保存失败：${e.message}", fatal = true)) }
            }
            running = false
        }
    }

    private fun fail(msg: String) {
        post(Status(running = false, fatal = true, message = msg))
        running = false
    }
}
