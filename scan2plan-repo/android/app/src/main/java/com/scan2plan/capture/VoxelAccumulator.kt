package com.scan2plan.capture

import kotlin.math.sqrt

/**
 * 体素去重累加器。
 *
 * 采集时每帧深度图会有几万个点，30 秒下来上百万点，直接堆内存会爆。
 * ARCore 深度图里同一个表面会被反复观测到，所以按 3cm 体素去重：
 * 每个体素只保留第一次落进去的点，既压内存又顺带去噪。
 */
class VoxelAccumulator(private val voxel: Float = 0.03f) {

    private val index = HashMap<Long, Int>(1 shl 16)
    private var buf = FloatArray(1 shl 18) // 容 8.7 万点，按需扩容
    private var capacityPoints = buf.size / 3

    var count: Int = 0
        private set

    /** 已累加点数上限（约 300 万点 ≈ 36MB），到顶后停止累加，避免 OOM。 */
    var maxPoints: Int = 3_000_000

    val isFull: Boolean get() = count >= maxPoints

    fun clear() {
        index.clear()
        count = 0
    }

    fun add(x: Float, y: Float, z: Float) {
        if (isFull) return
        val k = key(x, y, z)
        if (index.containsKey(k)) return
        if (count >= capacityPoints) grow()
        index[k] = count
        val o = count * 3
        buf[o] = x
        buf[o + 1] = y
        buf[o + 2] = z
        count++
    }

    private fun grow() {
        val bigger = FloatArray(buf.size * 2)
        System.arraycopy(buf, 0, bigger, 0, count * 3)
        buf = bigger
        capacityPoints = buf.size / 3
    }

    fun copyXYZ(): FloatArray = buf.copyOf(count * 3)

    private fun key(x: Float, y: Float, z: Float): Long {
        val ix = Math.floor((x / voxel).toDouble()).toInt().toLong() and 0x1FFFFF
        val iy = Math.floor((y / voxel).toDouble()).toInt().toLong() and 0x1FFFFF
        val iz = Math.floor((z / voxel).toDouble()).toInt().toLong() and 0x1FFFFF
        return (ix shl 42) or (iy shl 21) or iz
    }

    /** 体素尺寸（导出时写进 PLY 注释，方便追溯）。 */
    fun voxelSize(): Float = voxel

    /** 粗略覆盖率估计：点数 × 体素面积，用于 UI 提示。 */
    fun approxArea(): Float = count * voxel * voxel

    fun extent(): FloatArray {
        if (count == 0) return FloatArray(6)
        var minX = Float.MAX_VALUE; var minY = Float.MAX_VALUE; var minZ = Float.MAX_VALUE
        var maxX = -Float.MAX_VALUE; var maxY = -Float.MAX_VALUE; var maxZ = -Float.MAX_VALUE
        for (i in 0 until count) {
            val o = i * 3
            val x = buf[o]; val y = buf[o + 1]; val z = buf[o + 2]
            if (x < minX) minX = x; if (x > maxX) maxX = x
            if (y < minY) minY = y; if (y > maxY) maxY = y
            if (z < minZ) minZ = z; if (z > maxZ) maxZ = z
        }
        return floatArrayOf(minX, minY, minZ, maxX, maxY, maxZ)
    }

    fun diagonal(): Float {
        val e = extent()
        val dx = e[3] - e[0]; val dy = e[4] - e[1]; val dz = e[5] - e[2]
        return sqrt(dx * dx + dy * dy + dz * dz)
    }
}
