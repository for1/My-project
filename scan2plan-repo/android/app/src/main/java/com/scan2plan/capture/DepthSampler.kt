package com.scan2plan.capture

import android.media.Image
import com.google.ar.core.ArCamera
import com.google.ar.core.Frame
import com.google.ar.core.Session
import com.google.ar.core.exceptions.NotYetAvailableException
import java.nio.ShortBuffer

/**
 * ARCore 深度图 → 世界坐标点云。
 *
 * 关键细节（真机上最容易踩的坑都在这）：
 *  1. ARCore 深度图是 **DEPTH16**，数值单位是毫米，表示沿**相机光轴**的距离。
 *  2. 深度图有自己的内参，必须用 `camera.getImageIntrinsics(session, DEPTH_IMAGE)`，
 *     不能用彩色相机/纹理的内参 —— 两者分辨率不同（深度通常 640×480）。
 *  3. 相机坐标系是 OpenGL 约定：+X 右、+Y 上、**-Z 朝前**。
 *     所以 unproject 出来是 (x, y, -d)。
 *  4. 深度置信度图同样是 16bit，取值 0/1/2 表示低/中/高，按需过滤低置信度。
 *  5. 图像坐标原点在左上角，而 y 轴向上，所以要取负号。
 */
object DepthSampler {

    data class SampleStats(
        var frames: Int = 0,
        var pointsAdded: Int = 0,
        var depthWidth: Int = 0,
        var depthHeight: Int = 0,
        var lastDepthMillis: Long = 0L,
        var supported: Boolean = false,
    )

    /**
     * 从一帧里采样深度点并写入累加器。
     *
     * @param stride 像素步长（2 = 隔一个取一个，精度/性能折中；1 最密）
     * @param minDepthM 最近有效距离（米）
     * @param maxDepthM 最远有效距离（米），ARCore 深度有效范围一般 0.2~5m
     * @param minConfidence 最小置信度（0/1/2）
     */
    fun sampleInto(
        frame: Frame,
        session: Session,
        acc: VoxelAccumulator,
        stride: Int = 2,
        minDepthM: Float = 0.25f,
        maxDepthM: Float = 4.5f,
        minConfidence: Int = 1,
        stats: SampleStats? = null,
    ): Int {
        val camera = frame.camera
        if (camera.trackingState != com.google.ar.core.TrackingState.TRACKING) return 0

        val depth: Image = try {
            frame.acquireDepthImage16Bits()
        } catch (e: NotYetAvailableException) {
            return 0
        }

        var conf: Image? = null
        try {
            conf = frame.acquireDepthConfidenceImage()
        } catch (e: NotYetAvailableException) {
            // 置信度图不是所有机型/模式都有，缺了就不过滤
            conf = null
        }

        var added = 0
        try {
            val w = depth.width
            val h = depth.height
            stats?.depthWidth = w
            stats?.depthHeight = h

            val intr = camera.getImageIntrinsics(session, ArCamera.ImageMetadata.DEPTH_IMAGE)
            val fx = intr.focalLength[0]
            val fy = intr.focalLength[1]
            val cx = intr.principalPoint[0]
            val cy = intr.principalPoint[1]
            if (fx <= 0f || fy <= 0f) return 0

            val pose = FloatArray(16)
            camera.pose.toMatrix(pose, 0)

            val dp = depth.planes[0]
            val depthBuf: ShortBuffer = dp.buffer.asShortBuffer()
            val depthRowStride = dp.rowStride
            val depthPixelStride = dp.pixelStride

            var confBuf: ShortBuffer? = null
            var confRowStride = 0
            var confPixelStride = 0
            if (conf != null) {
                val cp = conf.planes[0]
                confBuf = cp.buffer.asShortBuffer()
                confRowStride = cp.rowStride
                confPixelStride = cp.pixelStride
            }

            val minD = minDepthM
            val maxD = maxDepthM

            var row = 0
            while (row < h) {
                var col = 0
                while (col < w) {
                    if (confBuf != null) {
                        val ci = (row * confRowStride + col * confPixelStride) / 2
                        if (ci < confBuf.capacity() && confBuf.get(ci) < minConfidence) {
                            col += stride
                            continue
                        }
                    }
                    val di = (row * depthRowStride + col * depthPixelStride) / 2
                    if (di >= depthBuf.capacity()) {
                        col += stride
                        continue
                    }
                    val raw = depthBuf.get(di).toInt() and 0xFFFF
                    val d = raw * 0.001f // mm -> m
                    if (d < minD || d > maxD) {
                        col += stride
                        continue
                    }

                    // 相机坐标系（OpenGL：-Z 朝前，+Y 朝上）
                    val xc = (col + 0.5f - cx) / fx * d
                    val yc = -(row + 0.5f - cy) / fy * d
                    val zc = -d

                    // 相机 → 世界（列主序 4x4）
                    val wx = pose[0] * xc + pose[4] * yc + pose[8] * zc + pose[12]
                    val wy = pose[1] * xc + pose[5] * yc + pose[9] * zc + pose[13]
                    val wz = pose[2] * xc + pose[6] * yc + pose[10] * zc + pose[14]

                    val before = acc.count
                    acc.add(wx, wy, wz)
                    if (acc.count > before) added++
                    if (acc.isFull) break
                    col += stride
                }
                if (acc.isFull) break
                row += stride
            }

            stats?.let {
                it.frames += 1
                it.pointsAdded += added
                it.lastDepthMillis = System.currentTimeMillis()
            }
        } finally {
            @Suppress("DEPRECATION")
            depth.close()
            @Suppress("DEPRECATION")
            conf?.close()
        }
        return added
    }

    /** 深度图转成 8 位灰度 Bitmap，用于 UI 取景反馈。 */
    fun depthToPreview(frame: Frame, maxDepthM: Float = 4.0f): ByteArray? {
        val depth = try {
            frame.acquireDepthImage16Bits()
        } catch (e: NotYetAvailableException) {
            return null
        }
        return try {
            val w = depth.width
            val h = depth.height
            val p = depth.planes[0]
            val buf = p.buffer.asShortBuffer()
            val out = ByteArray(w * h)
            val denom = (maxDepthM * 1000f)
            for (y in 0 until h) {
                for (x in 0 until w) {
                    val i = (y * p.rowStride + x * p.pixelStride) / 2
                    if (i >= buf.capacity()) continue
                    val d = (buf.get(i).toInt() and 0xFFFF).toFloat()
                    val v = 1f - (d / denom).coerceIn(0f, 1f)
                    out[y * w + x] = (v * 255f).toInt().toByte()
                }
            }
            out
        } catch (e: Exception) {
            null
        } finally {
            @Suppress("DEPRECATION")
            depth.close()
        }
    }
}
