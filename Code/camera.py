# camera.py

import os
import cv2
import numpy as np
from threading import Lock, Event
from picamera2 import Picamera2
import time

script_dir = os.path.dirname(os.path.realpath(__file__))
model_weights = os.path.join(script_dir, "model", "mobilenet_iter_73000.caffemodel")
model_config = os.path.join(script_dir, "model", "deploy.prototxt")
class_labels_path = os.path.join(script_dir, "model", "labelmap_voc.prototxt")

picam2 = Picamera2()
picam2.configure(picam2.create_preview_configuration(main={"format": 'XRGB8888', "size": (1280, 720)}))
net = cv2.dnn.readNetFromCaffe(model_config, model_weights)

frame_lock = Lock()
frame_available = Event()
latest_frame = None

picam2.start()

def capture_and_process():
    global latest_frame
    with open(class_labels_path, "r") as f:
        classes = f.read().strip().split("\n")

    try:
        while True:
            frame = picam2.capture_array()
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

            blob = cv2.dnn.blobFromImage(frame, 0.007843, (300, 300), 127.5)
            net.setInput(blob)
            detections = net.forward()

            for i in range(detections.shape[2]):
                confidence = detections[0, 0, i, 2]
                if confidence > 0.5:
                    class_id = int(detections[0, 0, i, 1])
                    if class_id > 0:  # Cat class: 8, person class: 15
                        box = detections[0, 0, i, 3:7] * np.array(
                            [frame.shape[1], frame.shape[0], frame.shape[1], frame.shape[0]])
                        (startX, startY, endX, endY) = box.astype("int")
                        cv2.rectangle(frame, (startX, startY), (endX, endY), (0, 255, 0), 2)
                        cv2.putText(frame, str(classes[class_id]) + ": "+ str(class_id), (startX, startY - 15),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

            with frame_lock:
                latest_frame = frame.copy()
                frame_available.set()
    finally:
        picam2.stop()

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
