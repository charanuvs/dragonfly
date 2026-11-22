package com.dragonfly.tabletstream

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Binder
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import com.pedro.common.ConnectChecker
import com.pedro.rtspserver.RtspServerStream
import com.pedro.library.util.sources.audio.MicrophoneSource
import com.pedro.library.util.sources.video.Camera2Source
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.ServerSocket
import java.net.Socket
import org.json.JSONObject

class CameraStreamService : Service(), ConnectChecker {
    companion object {
        const val ACTION_START = "com.dragonfly.tabletstream.START"
        const val ACTION_STOP = "com.dragonfly.tabletstream.STOP"
        private const val CHANNEL_ID = "camera_stream"
        private const val NOTIFICATION_ID = 1
        private const val PORT = 8554
        private const val CONTROL_PORT = 8555
        private const val CONTROL_CLIENT = "100.72.242.83"
        private const val WIDTH = 640
        private const val HEIGHT = 480
        private const val VIDEO_BITRATE = 800_000
        private const val TAG = "DragonflyCamera"
    }

    inner class LocalBinder : Binder() {
        val service: CameraStreamService
            get() = this@CameraStreamService
    }

    private val binder = LocalBinder()
    @Volatile private var stream: RtspServerStream? = null
    private var wakeLock: PowerManager.WakeLock? = null
    @Volatile private var cameraFacing = "Back"
    @Volatile private var controlServer: ServerSocket? = null
    @Volatile private var controlThread: Thread? = null
    @Volatile private var streamStatus = "Stopped"
    @Volatile private var streamUrl: String? = null
    private var stateListener: ((String, String?) -> Unit)? = null

    val currentStatus: String
        get() = streamStatus

    val currentUrl: String?
        get() = streamUrl

    val isStreaming: Boolean
        get() = stream != null

    val currentCameraFacing: String
        get() = cameraFacing

    fun switchCamera(): String {
        val cameraSource = (stream?.videoSource as? Camera2Source) ?: return cameraFacing
        cameraSource.switchCamera()
        cameraFacing = if (cameraFacing == "Back") "Front" else "Back"
        return cameraFacing
    }

    fun setCameraFacing(facing: String): String {
        val requested = if (facing.equals("front", ignoreCase = true)) "Front" else "Back"
        if (cameraFacing != requested) switchCamera()
        return cameraFacing
    }

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        startControlServer()
    }
    private fun startControlServer() {
        if (controlThread?.isAlive == true) return
        controlThread = Thread({
            try {
                val server = ServerSocket(CONTROL_PORT)
                server.reuseAddress = true
                controlServer = server
                while (!server.isClosed) {
                    try {
                        server.accept().use { socket ->
                            if (socket.inetAddress.hostAddress == CONTROL_CLIENT) {
                                socket.soTimeout = 5000
                                handleControlRequest(socket)
                            }
                        }
                    } catch (error: Exception) {
                        if (!server.isClosed) Log.w(TAG, "Camera control request failed", error)
                    }
                }
            } catch (error: Exception) {
                Log.e(TAG, "Could not start camera control server", error)
            }
        }, "DragonflyCameraControl").apply { isDaemon = true; start() }
    }

    private fun stopControlServer() {
        try { controlServer?.close() } catch (_: Exception) {}
        controlServer = null
        controlThread = null
    }

    private fun handleControlRequest(socket: Socket) {
        val reader = BufferedReader(InputStreamReader(socket.getInputStream()))
        val requestLine = reader.readLine() ?: return
        val parts = requestLine.split(" ")
        if (parts.size < 2) return
        val method = parts[0]
        val path = parts[1]

        val (statusCode, statusText, payload) = when {
                method == "GET" && path == "/status" -> Triple(
                    200,
                    "OK",
                    JSONObject().put("streaming", isStreaming).put("camera", cameraFacing).put("status", streamStatus).toString(),
            )
            method == "POST" && (path == "/camera/front" || path == "/camera/back") -> {
                if (!isStreaming) {
                    Triple(409, "Conflict", "{\"detail\":\"Stream is not running\"}")
                } else {
                    val facing = if (path.endsWith("front")) "Front" else "Back"
                    val result = setCameraFacing(facing)
                    Triple(200, "OK", "{\"camera\":\"${result}\"}")
                }
            }
            else -> Triple(404, "Not Found", "{\"detail\":\"Not found\"}")
        }

        val body = payload.toByteArray(Charsets.UTF_8)
        val output = socket.getOutputStream()
        output.write("HTTP/1.1 $statusCode $statusText\r\n".toByteArray())
        output.write("Content-Type: application/json\r\n".toByteArray())
        output.write("Content-Length: ${body.size}\r\n".toByteArray())
        output.write("Connection: close\r\n\r\n".toByteArray())
        output.write(body)
        output.flush()
    }

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopStream()
                stopSelf()
            }
            ACTION_START -> startStream()
        }
        return START_NOT_STICKY
    }

    fun setStateListener(listener: ((String, String?) -> Unit)?) {
        stateListener = listener
        listener?.invoke(streamStatus, streamUrl)
    }

    private fun startStream() {
        if (stream != null) return
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            updateState("Camera permission is required", null)
            stopSelf()
            return
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            updateState("Microphone permission is required", null)
            stopSelf()
            return
        }

        startAsForeground(buildNotification("Starting camera stream"))
        val server = RtspServerStream(
            applicationContext,
            PORT,
            this,
            Camera2Source(applicationContext),
            MicrophoneSource(),
        )
        cameraFacing = "Back"
        stream = server
        try {
            server.getStreamClient().setOnlyVideo(true)
            server.getStreamClient().setLogs(false)
            check(server.prepareVideo(WIDTH, HEIGHT, VIDEO_BITRATE)) {
                "This device could not prepare its camera encoder"
            }
            check(server.prepareAudio(32_000, true, 128_000)) {
                "This device could not prepare its audio encoder"
            }

            acquireWakeLock()
            server.startStream()
            val url = server.getStreamClient().getEndPointConnection()
            updateState("Streaming; waiting for viewer", url)
            updateNotification("Camera stream is active")
        } catch (error: Exception) {
            Log.e(TAG, "Could not start RTSP stream", error)
            try {
                server.release()
            } catch (releaseError: Exception) {
                Log.w(TAG, "Could not release failed stream", releaseError)
            }
            stream = null
            releaseWakeLock()
            updateState("Stream failed: ${error.message ?: "camera unavailable"}", null)
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
        }
    }

    private fun stopStream() {
        val current = stream
        stream = null
        try {
            current?.stopStream()
            current?.release()
        } catch (error: Exception) {
            Log.w(TAG, "Error stopping RTSP stream", error)
        }
        releaseWakeLock()
        streamUrl = null
        updateState("Stopped", null)
        stopForeground(STOP_FOREGROUND_REMOVE)
    }

    private fun startAsForeground(notification: Notification) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA,
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Camera stream",
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = "Shown while Dragonfly Camera is streaming"
            }
            getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        }
    }

    private fun buildNotification(message: String): Notification {
        val openApp = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val stopStream = PendingIntent.getService(
            this,
            1,
            Intent(this, CameraStreamService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, CHANNEL_ID)
        } else {
            Notification.Builder(this)
        }
        return builder
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .setContentTitle("Dragonfly Camera")
            .setContentText(message)
            .setContentIntent(openApp)
            .setOngoing(true)
            .addAction(android.R.drawable.ic_media_pause, "Stop", stopStream)
            .build()
    }

    private fun updateNotification(message: String) {
        getSystemService(NotificationManager::class.java)
            .notify(NOTIFICATION_ID, buildNotification(message))
    }

    private fun updateState(status: String, url: String?) {
        streamStatus = status
        streamUrl = url
        stateListener?.invoke(status, url)
    }

    private fun acquireWakeLock() {
        val powerManager = getSystemService(PowerManager::class.java)
        wakeLock = powerManager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "$TAG:Stream").apply {
            setReferenceCounted(false)
            acquire()
        }
    }

    private fun releaseWakeLock() {
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
    }

    override fun onDestroy() {
        stopControlServer()
        stopStream()
        super.onDestroy()
    }

    override fun onConnectionStarted(url: String) {
        Log.i(TAG, "RTSP client requested $url")
    }

    override fun onConnectionSuccess() {
        updateState("Viewer connected", streamUrl)
    }

    override fun onConnectionFailed(reason: String) {
        Log.e(TAG, "RTSP server error: $reason")
        updateState("Stream error: $reason", streamUrl)
    }

    override fun onNewBitrate(bitrate: Long) = Unit

    override fun onDisconnect() {
        if (stream != null) updateState("Streaming; waiting for viewer", streamUrl)
    }

    override fun onAuthError() {
        updateState("Viewer authentication failed", streamUrl)
    }

    override fun onAuthSuccess() = Unit
}
