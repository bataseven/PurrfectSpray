# camera.py

import os
import cv2
import numpy as np
from threading import Lock, Event
from picamera2 import Picamera2
import time
import logging
from logging.handlers import RotatingFileHandler
from detectors import MobileNetDetector, YoloV5Detector


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


try:
    picam2 = Picamera2()
    picam2.configure(picam2.create_preview_configuration(main={"format": 'XRGB8888', "size": (1280, 720)}))
    picam2.start()
except Exception as e:
    logger.exception("Failed to initialize camera")
    raise RuntimeError("Camera initialization failed") from e

detector = YoloV5Detector(model_name='yolov5n', conf_threshold=0.3)
# detector = MobileNetDetector()

frame_lock = Lock()
frame_available = Event()
latest_frame = None
def capture_and_process():
    frame_count = 0
    start_time = time.time()
    last_fps_time = time.time()
    end_time = time.time()
    global latest_frame
    fps = 0
    DETECTION_INTERVAL = 3
    detections = []

    while True:
        try:
            frame = picam2.capture_array()
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

            # Resize frame for processing 1/3 the size of current frame
            small = cv2.resize(frame, (0, 0), fx=1/2, fy=1/2)
            frame_count += 1
            
            if frame_count % DETECTION_INTERVAL == 0:
                detections = detector.detect(frame)
                
                current_time = time.time()
                elapsed = current_time - last_fps_time 
                fps = DETECTION_INTERVAL / elapsed if elapsed > 0 else 0
                last_fps_time = current_time

            for det in detections:
                startX, startY, endX, endY = det.box
                # Put a different color rectangle around the detected object
                color = (0, 255, 0) if det.class_id == 15 else (255, 0, 0)
                cv2.rectangle(frame, (startX, startY), (endX, endY), color, 2)
                label = f"{det.label}: {det.confidence:.2f}"
                cv2.putText(frame, label, (startX, startY - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

                if det.class_id == 15:  # Person class
                    target_x = int((startX + endX) / 2)
                    target_y = int((startY + endY) / 2)
                    cv2.circle(frame, (target_x, target_y), 5, (255, 0, 0), -1)
                    cv2.putText(frame, "Target", (target_x, target_y - 15),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 2)
                    
            fps = 1 / elapsed if elapsed > 0 else 0
                
            cv2.putText(frame, f"FPS: {fps:.1f}", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

            with frame_lock:
                latest_frame = frame.copy()
                frame_available.set()

        except Exception as e:
            logger.exception("Exception in capture_and_process loop")
            time.sleep(2)


def generate_frames():
    while True:
        got_frame = frame_available.wait(timeout=0.1)
        with frame_lock:
            if latest_frame is None:
                continue
            ret, buffer = cv2.imencode('.jpg', latest_frame)
            frame = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        if got_frame:
            frame_available.clear()
        time.sleep(1 / 20.0)  # limit to 20 FPS
