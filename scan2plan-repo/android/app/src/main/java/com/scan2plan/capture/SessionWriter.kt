package com.scan2plan.capture

import android.os.Build
import org.json.JSONObject
import java.io.BufferedOutputStream
import java.io.File
import java.io.FileOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 采集会话的输出：一个目录里放
 *
 *   cloud.ply        二进制 PLY 点云（世界坐标，米，Z 向上）—— 直接喂给 scan2plan recon
 *   poses.jsonl      每帧相机位姿（可选的轨迹信息，用于排查与二次优化）
 *   session.json     设备/参数/统计元数据
 *   video.mp4        可选：相机录像（需要同时开 CameraX，默认关）
 *
 * PLY 用 binary_little_endian，体积小、加载快，scan2plan 与 MeshLab/CloudCompare 都能直接读。
 */
class SessionWriter(private val rootDir: File) {

    val sessionDir: File = File(rootDir, "scan_" + SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date()))

    val cloudFile: File get() = File(sessionDir, "cloud.ply")
    val posesFile: File get() = File(sessionDir, "poses.jsonl")
    val metaFile: File get() = File(sessionDir, "session.json")

    private val poses = ArrayList<String>(2048)

    init {
        if (!sessionDir.exists()) sessionDir.mkdirs()
    }

    fun recordPose(timestampNs: Long, mat: FloatArray, trackingState: String) {
        if (poses.size > 20000) return
        val sb = StringBuilder(256)
        sb.append("{\"t\":").append(timestampNs)
            .append(",\"state\":\"").append(trackingState).append("\"")
            .append(",\"m\":[")
        for (i in 0 until 16) {
            if (i > 0) sb.append(',')
            sb.append(String.format(Locale.US, "%.6f", mat[i]))
        }
        sb.append("]}")
        poses.add(sb.toString())
    }

    fun flushPoses() {
        if (poses.isEmpty()) return
        posesFile.bufferedWriter().use { w ->
            for (line in poses) {
                w.write(line)
                w.write("\n")
            }
        }
    }

    /** 写二进制 PLY。 */
    fun writeCloud(acc: VoxelAccumulator): Long {
        val xyz = acc.copyXYZ()
        val n = acc.count
        val header = buildString {
            append("ply\n")
            append("format binary_little_endian 1.0\n")
            append("comment scan2plan capture, units=meters, Z-up, voxel=${acc.voxelSize()}\n")
            append("element vertex $n\n")
            append("property float x\n")
            append("property float y\n")
            append("property float z\n")
            append("end_header\n")
        }
        // 注意：DataOutputStream.writeInt 是**大端**，而 PLY 要求小端。
        // 必须用 ByteBuffer 显式指定 LITTLE_ENDIAN，否则导出的点云数值全是乱码。
        val body = ByteBuffer.allocate(n * 12).order(ByteOrder.LITTLE_ENDIAN)
        for (i in 0 until n) {
            val o = i * 3
            body.putFloat(xyz[o])
            body.putFloat(xyz[o + 1])
            body.putFloat(xyz[o + 2])
        }
        FileOutputStream(cloudFile).use { fos ->
            BufferedOutputStream(fos, 1 shl 16).use { bos ->
                bos.write(header.toByteArray(Charsets.US_ASCII))
                bos.write(body.array(), 0, body.position())
                bos.flush()
            }
        }
        return cloudFile.length()
    }

    fun writeMeta(
        acc: VoxelAccumulator,
        stats: DepthSampler.SampleStats,
        depthMode: String,
        extra: Map<String, Any?> = emptyMap(),
    ) {
        val e = acc.extent()
        val json = JSONObject()
        json.put("format", "scan2plan.capture/1")
        json.put("created_at", SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).format(Date()))
        json.put("device", "${Build.MANUFACTURER} ${Build.MODEL}")
        json.put("android", Build.VERSION.RELEASE)
        json.put("sdk", Build.VERSION.SDK_INT)
        json.put("depth_mode", depthMode)
        json.put("units", "meters")
        json.put("up_axis", "Z")
        json.put("voxel_size_m", acc.voxelSize().toDouble())
        json.put("points", acc.count)
        json.put("depth_frames", stats.frames)
        json.put("depth_size", "${stats.depthWidth}x${stats.depthHeight}")
        json.put("bbox_min", listOf(e[0].toDouble(), e[1].toDouble(), e[2].toDouble()))
        json.put("bbox_max", listOf(e[3].toDouble(), e[4].toDouble(), e[5].toDouble()))
        json.put("diagonal_m", acc.diagonal().toDouble())
        json.put("pose_count", poses.size)
        for ((k, v) in extra) json.put(k, v)
        json.put("note", "用 `python -m scan2plan recon <这个目录>/cloud.ply -o out.json` 生成户型图")
        metaFile.writeText(json.toString(2))
    }
}
