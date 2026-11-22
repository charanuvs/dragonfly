# Dragonfly Camera

A minimal Android RTSP camera source for the Dragonfly hub. It serves video only at `rtsp://<tablet-tailscale-ip>:8554/`; Dragonfly on phila pulls the stream on demand and records only when configured.

## Build

Requirements: JDK 17, Android SDK 35, and Gradle wrapper (8.11.1).

```sh
./gradlew assembleDebug
```

APK: `app/build/outputs/apk/debug/app-debug.apk`.

## Use

On the tablet while connected to Tailscale, open `https://phila.int` and tap **Tablet camera APK** in the Devices heading. Android may ask you to allow app installs from the browser; approve that prompt, then install the downloaded APK.

Open Dragonfly Camera, grant Camera, Microphone and Notifications permissions as requested, then tap **Start stream**. The streaming library requires microphone permission to initialize, but the RTSP stream transmits video only. It runs as a camera foreground service with an ongoing notification and a Stop action. Streaming does not start automatically on boot. The dashboard card appears as online when the app is streaming and provides the front/back selector. The RTSP address is `rtsp://100.91.89.75:8554/`.

The RTSP listener is available on the tablet's interfaces. Use the Tailscale address from the app's URL for phila; do not configure router port forwarding. Streaming starts only after the user taps Start and does not auto-start on boot.

Camera control uses port 8555 and accepts connections only from phila's Tailscale address, `100.72.242.83`. If that address changes, update `CONTROL_CLIENT` and rebuild the APK. The configured tablet live view adds a local-time watermark on phila.
