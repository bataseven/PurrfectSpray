# camera.py

import os
import cv2
import numpy as np
from threading import Lock, Event
from picamera2 import Picamera2
import time
import logging
from logging.handlers import RotatingFileHandler
from detectors import MobileNetDetector, YoloV5Detector, YoloV5VinoDetector, highlight_colors, ActiveObjectTracker, Detection
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

xml = os.path.expanduser(
    "~/Desktop/PurrfectSpray/Code/models/openvino_model/yolov5n.xml"
)
frame_lock = Lock()
frame_available = Event()
latest_frame = None

def tiled_detect(frame, detector, tile_size=(1280, 1280), overlap=200):
    """
    Run detector.detect() on overlapping tiles of the input frame,
    then re-offset all boxes back into full-frame coords,
    and finally apply a global NMS to collapse duplicates.
    
    Returns: List[Detection]
    """
    h, w = frame.shape[:2]
    tw, th = tile_size
    step_x = tw - overlap
    step_y = th - overlap

    all_dets = []
    for x in range(0, w, step_x):
        for y in range(0, h, step_y):
            x2 = min(x + tw, w)
            y2 = min(y + th, h)
            tile = frame[y:y2, x:x2]
            if tile.size == 0:
                continue

            # 1) detect on the tile
            dets = detector.detect(tile)

            # 2) re-offset each box into full-frame coords
            for d in dets:
                bx1, by1, bx2, by2 = d.box
                nx1 = bx1 + x
                ny1 = by1 + y
                nx2 = bx2 + x
                ny2 = by2 + y
                # clamp
                nx1, ny1 = max(0, nx1), max(0, ny1)
                nx2, ny2 = min(w, nx2), min(h, ny2)
                all_dets.append(
                    Detection(d.class_id, d.label, d.confidence, (nx1, ny1, nx2, ny2))
                )

    # if nothing found, bail out
    if not all_dets:
        return []

    # 3) build arrays for global NMS
    raw_boxes   = []
    confidences = []
    class_ids   = []
    for d in all_dets:
        x1, y1, x2, y2 = d.box
        raw_boxes.append([x1, y1, x2 - x1, y2 - y1])  # x, y, w, h
        confidences.append(d.confidence)
        class_ids.append(d.class_id)

    # 4) run a single NMS over everything
    #    use the detector's own thresholds if available
    conf_thresh = getattr(detector, "conf_threshold", 0.5)
    nms_thresh  = getattr(detector, "nms_threshold", 0.45)
    indices = cv2.dnn.NMSBoxes(raw_boxes, confidences, conf_thresh, nms_thresh)

    final = []
    if len(indices):
        for i in indices.flatten():
            d = all_dets[i]
            final.append(d)

    return final



def capture_and_process():
    global latest_frame, latest_detections

    while not app_state.shutdown_event.is_set():
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
            detector = YoloV5Detector(model_name='yolov5n', conf_threshold=0.3, size=1280)
            print("Using YOLOv5n detector")
        elif model_name == 'openvino':
            detector = YoloV5VinoDetector(xml_path=xml, conf_threshold=0.3)
            print("Using OpenVINO YOLOv5 detector")
        else:
            raise ValueError(f"Unknown model: {model_name}")
        
        
# one tracker per background thread
# CSRT is a good choice for fast-moving objects
# KCF is faster but less accurate
# MIL is the fastest but least accurate
tracker = ActiveObjectTracker(tracker_type="CSRT", max_track_frames=8, max_misses=4)

def detect_in_background():
    global latest_frame, latest_detections

    # wait until the very first frame arrives
    while latest_frame is None and not app_state.shutdown_event.is_set():
        time.sleep(0.05)

    while not app_state.shutdown_event.is_set():
        loop_start = time.perf_counter()

        try:
            # pull a fresh copy of the frame
            with frame_lock:
                frame = latest_frame.copy()

            # --- 1) measure inference time only ---
            with detector_lock:
                if detector:
                    detect_start = time.perf_counter()
                    dets = tiled_detect(
                    frame,
                    detector,
                    tile_size=(1280, 1280),
                    overlap=200
                ) if detector else []
                    infer_ms = (time.perf_counter() - detect_start) * 1e3
                else:
                    dets = []
                    infer_ms = 0.0

            # normalize boxes to ints
            for d in dets:
                x1, y1, x2, y2 = d.box
                d.box = (int(x1), int(y1), int(x2), int(y2))

            # save raw detections if you still need them
            with detection_lock:
                latest_detections = dets

            # If you do tiling, sum up each tile’s time:
            # total_tile_ms = 0
            # for tile in tiles:
            #     t0 = time.perf_counter()
            #     partial_dets = detector.detect(tile)
            #     total_tile_ms += (time.perf_counter() - t0) * 1e3
            # infer_ms = total_tile_ms

            # decide which class we’re actively tracking
            target = app_state.tracking_target.lower() if app_state.tracking_target else None
            if not target:
                tracker.clear()
                app_state.target_lock.clear()
            else:
                output = tracker.update(
                    frame,
                    [ {'bbox':d.box, 'class':d.label.lower(), 'conf':d.confidence}
                      for d in dets ],
                    target_class=target,
                    conf_thresh=0.3
                )
                if tracker.last_box:
                    x,y,w,h = tracker.last_box
                    cx, cy = x + w//2, y + h//2
                    app_state.latest_target_coords = (cx, cy)
                    app_state.target_lock.set()
                else:
                    app_state.target_lock.clear()

            # --- 2) measure total loop time ---
            loop_ms = (time.perf_counter() - loop_start) * 1e3
            fps = 1000.0 / loop_ms if loop_ms > 0 else float('inf')

            # logger.info(f"Inference: {infer_ms:.1f} ms, total loop: {loop_ms:.1f} ms, FPS: {fps:.1f}")

        except Exception:
            logger.exception("Exception in detect_in_background")
            time.sleep(1)
            

FRAME_PUB_PORT = int(os.getenv("FRAME_PUB_PORT", 5555))

# Set up ZMQ publisher
context = zmq.Context()
socket = context.socket(zmq.PUB)
socket.bind(f"tcp://*:{FRAME_PUB_PORT}")  # Bind to localhost port 5555


def stream_frames_over_zmq():
    global latest_frame
    while not app_state.shutdown_event.is_set():
        with frame_lock:
            if latest_frame is not None:
                _, buffer = cv2.imencode(".jpg", latest_frame)
                jpg_bytes = base64.b64encode(buffer)
                socket.send(jpg_bytes)
        time.sleep(1 / 30.0)
