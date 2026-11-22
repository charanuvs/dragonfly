package com.dragonfly.tabletstream

import android.Manifest
import android.app.Activity
import android.content.ClipData
import android.content.ClipboardManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast

class MainActivity : Activity() {
    private lateinit var statusView: TextView
    private lateinit var urlView: TextView
    private lateinit var streamButton: Button
    private lateinit var copyButton: Button
    private var streamService: CameraStreamService? = null
    private var bound = false

    private val stateListener: (String, String?) -> Unit = { status, url ->
        runOnUiThread { renderState(status, url) }
    }

    private val serviceConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            streamService = (binder as? CameraStreamService.LocalBinder)?.service
            streamService?.setStateListener(stateListener)
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            streamService?.setStateListener(null)
            streamService = null
            renderState("Service disconnected", null)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        buildUi()
    }

    override fun onStart() {
        super.onStart()
        bound = bindService(
            Intent(this, CameraStreamService::class.java),
            serviceConnection,
            Context.BIND_AUTO_CREATE,
        )
    }

    override fun onStop() {
        streamService?.setStateListener(null)
        if (bound) {
            unbindService(serviceConnection)
            bound = false
        }
        streamService = null
        super.onStop()
    }

    private fun buildUi() {
        val padding = (24 * resources.displayMetrics.density).toInt()
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(padding, padding, padding, padding)
            setBackgroundColor(0xFF101820.toInt())
        }

        val title = TextView(this).apply {
            text = "Dragonfly Camera"
            textSize = 26f
            setTextColor(0xFFF4F6EE.toInt())
        }
        statusView = TextView(this).apply {
            text = "Stopped"
            textSize = 18f
            setTextColor(0xFF9ED7B5.toInt())
            setPadding(0, padding, 0, padding / 2)
        }
        urlView = TextView(this).apply {
            text = ""
            textSize = 15f
            setTextColor(0xFFE0E7E3.toInt())
            setTextIsSelectable(true)
            setPadding(0, 0, 0, padding)
        }
        streamButton = Button(this).apply {
            text = "Start stream"
            setOnClickListener { onStreamButtonPressed() }
        }
        copyButton = Button(this).apply {
            text = "Copy RTSP address"
            isEnabled = false
            setOnClickListener { copyStreamUrl() }
        }
        val hint = TextView(this).apply {
            text = "Start while this app is open. Streaming continues in the foreground with a persistent notification."
            textSize = 14f
            setTextColor(0xFFB7C2C0.toInt())
            setPadding(0, padding, 0, 0)
        }

        root.addView(title, matchWrap())
        root.addView(statusView, matchWrap())
        root.addView(urlView, matchWrap())
        root.addView(streamButton, matchWrap())
        root.addView(copyButton, matchWrap())
        root.addView(hint, matchWrap())
        setContentView(root)
    }

    private fun matchWrap() = LinearLayout.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT,
        ViewGroup.LayoutParams.WRAP_CONTENT,
    )

    private fun onStreamButtonPressed() {
        if (streamService?.isStreaming == true) {
            startService(Intent(this, CameraStreamService::class.java).setAction(CameraStreamService.ACTION_STOP))
            return
        }
        requestStreamPermissions()
    }

    private fun requestStreamPermissions() {
        val required = mutableListOf(Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            required += Manifest.permission.POST_NOTIFICATIONS
        }
        val missing = required.filter {
            checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isEmpty()) {
            startStreamingService()
        } else {
            requestPermissions(missing.toTypedArray(), REQUEST_PERMISSIONS)
        }
    }

    @Deprecated("Use the activity result API when this app gains more permission flows")
    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != REQUEST_PERMISSIONS) return
        if (grantResults.isNotEmpty() && grantResults.all { it == PackageManager.PERMISSION_GRANTED }) {
            startStreamingService()
        } else {
            Toast.makeText(this, "Camera and notification permissions are needed to stream", Toast.LENGTH_LONG).show()
        }
    }

    private fun startStreamingService() {
        val intent = Intent(this, CameraStreamService::class.java).setAction(CameraStreamService.ACTION_START)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }
        if (!bound) {
            bound = bindService(
                Intent(this, CameraStreamService::class.java),
                serviceConnection,
                Context.BIND_AUTO_CREATE,
            )
        }
        renderState("Starting stream", null)
    }

    private fun renderState(status: String, url: String?) {
        if (!::statusView.isInitialized) return
        statusView.text = status
        urlView.text = url.orEmpty()
        streamButton.text = if (streamService?.isStreaming == true) "Stop stream" else "Start stream"
    }

    private fun copyStreamUrl() {
        val url = streamService?.currentUrl ?: return
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        clipboard.setPrimaryClip(ClipData.newPlainText("RTSP stream URL", url))
        Toast.makeText(this, "RTSP address copied", Toast.LENGTH_SHORT).show()
    }

    companion object {
        private const val REQUEST_PERMISSIONS = 100
    }
}
