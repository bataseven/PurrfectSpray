# PurrfectSpray 🐱💦🔴

**PurrfectSpray** is an AI-powered, cat-tracking deterrent system that combines real-time object detection, motorized laser targeting, and optional water spraying — all controlled through a web interface. The system runs on a Raspberry Pi 5 and features low-latency WebRTC streaming for seamless interaction, even when hosted online.

---

## 🚀 Features

- 🧠 Real-time cat detection with YOLOv5 Nano (`yolov5n.pt`)
- 🌐 Web interface served via Flask + Socket.IO
- 🎯 2-axis stepper-motor-driven gimbal
- 🔴 Red laser pointer targeting
- 💦 Optional water spray trigger when a cat is detected
- 🔌 Modular backend: `app.py` (control logic) + `webrtc_stream.py` (video streaming)
- 📡 WebRTC support for <1s latency remote video
- 🛠️ 3D-printable gimbal (see `CAD/` folder)
- 📝 Detailed logging for system and camera events

---

## 🛠 Tech Stack

- Raspberry Pi 5 with Camera Module v1.3 (or compatible)
- Flask + Flask-SocketIO
- aiortc for WebRTC-based low-latency streaming
- OpenCV, NumPy
- AccelStepper-compatible stepper motor control
- YOLOv5 Nano model

---

## 📦 Installation & Usage

### 1. Clone the repository

```bash
git clone https://github.com/bataseven/PurrfectSpray.git
cd PurrfectSpray
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

*Make sure `ffmpeg` and `libavdevice` are also installed on your Pi for WebRTC.*

---

## ⚙️ Running the System

### Local LAN Usage (with Flask only)

```bash
python app.py
```

Access via: `http://<raspberry-pi-ip>:5000`

### Hosted Online with WebRTC (Recommended for Remote Use)

You’ll need to run both `app.py` and `webrtc_stream.py`.

```bash
# Terminal 1 (Flask server and object detection)
python app.py

# Terminal 2 (WebRTC video stream)
python webrtc_stream.py
```

Then open the exposed URL (e.g., via Cloudflare Tunnel) in a browser.


## 📄 License

MIT License
