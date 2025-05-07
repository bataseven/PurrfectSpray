# camera.py

import os
import cv2
import numpy as np
from threading import Lock, Event
from picamera2 import Picamera2
import time
import logging
from logging.handlers import RotatingFileHandler
from detectors import MobileNetDetector, YoloV5Detector, YoloV5OVDetector, highlight_colors
import threading
from app_state import app_state, MotorMode
import zmq
import base64
import cv2

logger = logging.getLogger("Camera")
logger.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
logger.addHandler(console_handler)

file_handler = RotatingFileHandler(
    "camera.log", maxBytes=512*1024, backupCount=3)
file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
logger.addHandler(file_handler)

script_dir = os.path.dirname(os.path.realpath(__file__))
model_weights = os.path.join(    script_dir, "model", "mobilenet_iter_73000.caffemodel")
model_config = os.path.join(script_dir, "model", "deploy.prototxt")
class_labels_path = os.path.join(script_dir, "model", "labelmap_voc.prototxt")

latest_detections = []
detection_lock = threading.Lock()
detector_lock = threading.Lock()

picam2 = None

try:
    picam2 = Picamera2()
    picam2.configure(picam2.create_preview_configuration(
        main={"format": 'XRGB8888', "size": (1920, 1080)}))
    picam2.start()
except Exception as e:
    logger.exception("Failed to initialize camera")
    raise RuntimeError("Camera initialization failed") from e

detector = None

openvino_model_path = os.path.join(script_dir,"yolov5nu_openvino_model")

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

            selected_label = app_state.tracking_target.lower() if app_state.tracking_target else None
            
            for i, det in enumerate(detections_copy):
                color = highlight_colors[det.class_id % len(highlight_colors)]
                if selected_label and det.label.lower() != selected_label:
                    # Put a sample circle on the detection
                    startX, startY, endX, endY = det.box
                    target_x = int((startX + endX) / 2)
                    target_y = int((startY + endY) / 2)
                    cv2.circle(frame, (target_x, target_y), 5, color, -1)
                    continue
                startX, startY, endX, endY = det.box
                cv2.rectangle(frame, (startX, startY), (endX, endY), color, 2)
                label = f"{det.label}: {det.confidence:.2f}"
                cv2.putText(frame, label, (startX, startY - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

            with frame_lock:
                latest_frame = frame.copy()
                frame_available.set()

        except Exception as e:
            logger.exception("Exception in capture_and_process loop")
            time.sleep(2)
           
            
def set_detector(model_name):
    global detector
    with detector_lock:
        if model_name is None:
            detector = None
            print("[Detector] Object detection disabled")
        elif model_name == 'mobilenet':
            detector = MobileNetDetector()
            print("Using MobileNet detector")
        elif model_name == 'yolov5n':
            detector = YoloV5Detector(model_name='yolov5n', conf_threshold=0.3, size=320)
            print("Using YOLOv5n detector")
        elif model_name == 'openvino':
            detector = YoloV5OVDetector(openvino_model_path)
            print("Using OpenVINO YOLOv5 detector")
        else:
            raise ValueError(f"Unknown model: {model_name}")
        
        
def detect_in_background():
    global latest_frame, latest_detections

    while latest_frame is None:
        time.sleep(0.05)

    while True:
        try:
            with detector_lock:
                if detector is None:
                    time.sleep(0.1)
                    continue
            
            with frame_lock:
                frame = latest_frame.copy()

            with detector_lock:
                if detector is not None:
                    detections = detector.detect(frame)

            for det in detections:
                x1, y1, x2, y2 = det.box
                # Ensure integer pixel values
                det.box = (int(x1), int(y1), int(x2), int(y2))
                # print(
                #     f"Detection: {det.label} ({det.confidence:.2f}) at ({x1}, {y1}, {x2}, {y2})")

            with detection_lock:
                latest_detections = detections

            for det in detections:
                if app_state.current_mode==MotorMode.TRACKING and det.label.lower() == app_state.tracking_target.lower():
                    x = int((det.box[0] + det.box[2]) / 2)
                    y = int((det.box[1] + det.box[3]) / 2)
                    app_state.latest_target_coords = (None, None)
                    # app_state.latest_target_coords = (x, y)
                    print(f"Detected target at ({x}, {y})")
                    app_state.target_lock.set()
                    break

        except Exception as e:
            logger.exception("Exception in detect_in_background")
            time.sleep(2)


FRAME_PUB_PORT = int(os.getenv("FRAME_PUB_PORT", 5555))

# Set up ZMQ publisher
context = zmq.Context()
socket = context.socket(zmq.PUB)
socket.bind(f"tcp://*:{FRAME_PUB_PORT}")  # Bind to localhost port 5555


def stream_frames_over_zmq():
    global latest_frame
    while True:
        with frame_lock:
            if latest_frame is not None:
                _, buffer = cv2.imencode(".jpg", latest_frame)
                jpg_bytes = base64.b64encode(buffer)
                socket.send(jpg_bytes)
        time.sleep(1 / 30.0)
