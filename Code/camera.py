# camera.py

import os
import cv2
import numpy as np
from threading import Lock, Event
from picamera2 import Picamera2
import time
import logging
from logging.handlers import RotatingFileHandler
from detectors import MobileNetDetector, YoloV5Detector, YoloV5OVDetector
import threading
from app_state import app_state
from flask_socketio import SocketIO
import zmq
import base64
import cv2
import __main__

logger = logging.getLogger("Camera")
logger.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
logger.addHandler(console_handler)

file_handler = RotatingFileHandler("camera.log", maxBytes=512*1024, backupCount=3)
file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
logger.addHandler(file_handler)

script_dir = os.path.dirname(os.path.realpath(__file__))
model_weights = os.path.join(script_dir, "model", "mobilenet_iter_73000.caffemodel")
model_config = os.path.join(script_dir, "model", "deploy.prototxt")
class_labels_path = os.path.join(script_dir, "model", "labelmap_voc.prototxt")

latest_detections = []
detection_lock = threading.Lock()


picam2 = None

try:
    if __main__.__file__.endswith("app.py"):
        from picamera2 import Picamera2
        picam2 = Picamera2()
        picam2.configure(picam2.create_preview_configuration(main={"format": 'XRGB8888', "size": (1920, 1080)}))
        picam2.start()
except Exception as e:
    logger.exception("Failed to initialize camera")
    raise RuntimeError("Camera initialization failed") from e

detector = YoloV5Detector(model_name='yolov5n', conf_threshold=0.3, size=320)
# detector = MobileNetDetector()
# detector = YoloV5OVDetector(model_path)

frame_lock = Lock()
frame_available = Event()
latest_frame = None

def capture_and_process():
    global latest_frame, latest_detections

    while True:
        try:
            frame = picam2.capture_array()
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

            # draw latest detections (from other thread)
            with detection_lock:
                detections_copy = list(latest_detections)

            for det in detections_copy:
                startX, startY, endX, endY = det.box
                color = (0, 255, 0) if det.class_id == 15 else (255, 0, 0)
                cv2.rectangle(frame, (startX, startY), (endX, endY), color, 2)
                label = f"{det.label}: {det.confidence:.2f}"
                cv2.putText(frame, label, (startX, startY - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

                if det.class_id == 15:
                    target_x = int((startX + endX) / 2)
                    target_y = int((startY + endY) / 2)
                    cv2.circle(frame, (target_x, target_y), 5, (255, 0, 0), -1)
                    cv2.putText(frame, "Target", (target_x, target_y - 15),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 2)

            with frame_lock:
                latest_frame = frame.copy()
                frame_available.set()

        except Exception as e:
            logger.exception("Exception in capture_and_process loop")
            time.sleep(2)


def encode_loop():
    
    while True:
        with frame_lock:
            if latest_frame is not None:
                ret, buffer = cv2.imencode('.jpg', latest_frame)
                if ret:
                    with app_state.jpeg_lock:
                        app_state.encoded_jpeg = buffer.tobytes()
        time.sleep(1 / 20.0)  # This controls the frame rate


def generate_frames():
    while True:
        with app_state.jpeg_lock:
            frame = app_state.encoded_jpeg
        if frame:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

# Set up ZMQ publisher
context = zmq.Context()
socket = context.socket(zmq.PUB)
socket.bind("tcp://*:5555")  # Bind to localhost port 5555

def stream_frames_over_zmq():
    global latest_frame
    while True:
        with frame_lock:
            if latest_frame is not None:
                _, buffer = cv2.imencode(".jpg", latest_frame)
                jpg_bytes = base64.b64encode(buffer)
                socket.send(jpg_bytes)
        time.sleep(1 / 30)

def detect_in_background():
    global latest_frame, latest_detections

    while latest_frame is None:
        time.sleep(0.05)

    while True:
        try:
            with frame_lock:
                frame = latest_frame.copy()

            detections = detector.detect(frame)

            # Scale factor: YOLO resizes input to detector.size (e.g. 640)
            scale_x = 1920 / detector.size
            scale_y = 1080 / detector.size

            scaled_detections = []
            for det in detections:
                x1, y1, x2, y2 = det.box
                # Rescale to match original resolution
                x1 = int(x1 * scale_x)
                y1 = int(y1 * scale_y)
                x2 = int(x2 * scale_x)
                y2 = int(y2 * scale_y)
                det.box = (x1, y1, x2, y2)
                scaled_detections.append(det)

            with detection_lock:
                latest_detections = scaled_detections

            for det in scaled_detections:
                if app_state.auto_mode and det.label.lower() == app_state.tracking_target.lower():
                    x = int((det.box[0] + det.box[2]) / 2)
                    y = int((det.box[1] + det.box[3]) / 2)
                    app_state.latest_target_coords = (x, y)
                    app_state.target_lock.set()  # 🔁 signal motor loop to move
                    break  # only track the first match

        except Exception as e:
            logger.exception("Exception in detect_in_background")
            time.sleep(2)
