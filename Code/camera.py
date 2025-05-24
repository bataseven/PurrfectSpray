# camera.py

import os
import cv2
import numpy as np
from threading import Lock, Event
from picamera2 import Picamera2
import time
import logging
from logging.handlers import RotatingFileHandler
from detectors import highlight_colors, Detection
from detectors import MobileNetDetector
from detectors import YoloV5Detector
from detectors import YoloV5VinoDetector
from detectors import YoloV8SegDetector
from detectors import YoloV8OpenVINOSegDetector
import threading
from app_state import app_state, GimbalState
import zmq
import base64
import cv2
from multi_tracker import Sort


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

yolov5_xml = os.path.expanduser(
    "~/Desktop/PurrfectSpray/Code/models/openvino_model/yolov5n.xml"
)

yolov8_xml = os.path.expanduser(
    "~/Desktop/PurrfectSpray/Code/models/yolov8n-seg_openvino_model/yolov8n-seg.xml"
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

            # grab snapshot of detections & tracked center
            with detection_lock:
                dets = list(latest_detections)
            tc = app_state.latest_target_coords
            tracked_center = (
                tc if tc and tc[0] is not None and tc[1] is not None else None
            )

            # draw all detections
            for det in dets:
                x1, y1, x2, y2 = det.box
                color = highlight_colors[det.class_id % len(highlight_colors)]
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(frame,
                            f"{det.label}: {det.confidence:.2f}",
                            (x1, y1 - 10),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.5,
                            color,
                            2)

            # now overlay TARGET on the one box whose center == tracked_center
            if tracked_center:
                tx, ty = tracked_center
                # find whichever detection is closest to that center
                for det in dets:
                    x1, y1, x2, y2 = det.box
                    cx = (x1 + x2) // 2
                    cy = (y1 + y2) // 2
                    if abs(cx - tx) < 5 and abs(cy - ty) < 5:
                        # bump the text up above the box
                        cv2.putText(frame,
                                    "TARGET",
                                    (x1, y1 - 25),
                                    cv2.FONT_HERSHEY_SIMPLEX,
                                    0.7,
                                    (0, 0, 255),
                                    2)
                        break

            # publish
            with frame_lock:
                latest_frame = frame.copy()
                frame_available.set()

        except Exception:
            logger.exception("Exception in capture_and_process loop")
            time.sleep(2)


           
            
def set_detector(model_name):
    global detector
    with detector_lock:
        if model_name is None:
            detector = None
            logger.info("[Detector] Object detection disabled")
        elif model_name == 'mobilenet':
            detector = MobileNetDetector()
            logger.info("Using MobileNet detector")
        elif model_name == 'yolov5n':
            detector = YoloV5Detector(model_name='yolov5n', conf_threshold=0.3, size=1280)
            logger.info("Using YOLOv5n detector")
        elif model_name == 'openvino':
            detector = YoloV5VinoDetector(xml_path=yolov5_xml, conf_threshold=0.3)
            logger.info("Using OpenVINO YOLOv5 detector")
        elif model_name == 'yolov8seg':
            detector = YoloV8SegDetector(model_path="yolov8n-seg.pt", conf_threshold=0.3)
            logger.info("Using YOLOv8 segmentation detector")
        elif model_name == "yolov8openvino":
            print("Starting YOLOv8 OpenVINO segmentation detector")
            detector = YoloV8OpenVINOSegDetector(xml_path=yolov8_xml, conf_threshold=0.3)
            logger.info("Using YOLOv8 OpenVINO segmentation detector")    
        else:
            raise ValueError(f"Unknown model: {model_name}")
        
        
multi_tracker = Sort(max_age=10, min_hits=1, iou_threshold=0.3)        

def detect_in_background():
    global latest_frame, latest_detections

    # wait for first frame
    while latest_frame is None and not app_state.shutdown_event.is_set():
        time.sleep(0.05)

    while not app_state.shutdown_event.is_set():
        loop_start = time.perf_counter()

        try:
            # 1) grab current frame copy
            with frame_lock:
                frame = latest_frame.copy()

            # 2) run (tiled) detection
            with detector_lock:
                if detector:
                    t0 = time.perf_counter()
                    if isinstance(detector, YoloV8SegDetector) or isinstance(detector, YoloV8OpenVINOSegDetector):
                        dets = detector.detect(frame, overlay=True)
                    else:
                        dets = tiled_detect(frame, detector, tile_size=(1280,1280), overlap=200)
    
                    infer_ms = (time.perf_counter() - t0)*1e3
                else:
                    dets, infer_ms = [], 0.0

            # 3) normalize boxes
            for d in dets:
                x1,y1,x2,y2 = d.box
                d.box = (int(x1),int(y1),int(x2),int(y2))

            # 4) snapshot for UI/debug
            with detection_lock:
                latest_detections = list(dets)

            # 5) build SORT input array
            if dets:
                dets_arr = np.array([[*d.box, d.confidence] for d in dets], dtype=np.float32)
            else:
                dets_arr = np.empty((0,5), dtype=np.float32)

            # 6) run SORT to get tracks: [[x1,y1,x2,y2,track_id],…]
            tracks = multi_tracker.update(dets_arr)

            # 7) map tracks back to Detection objects *only* if we have any dets
            tracked_objs = []
            if dets and tracks.shape[0] > 0:
                for x1, y1, x2, y2, tid in tracks:
                    # find which detection this corresponds to by center proximity
                    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                    best = min(
                        dets,
                        key=lambda d: ((d.box[0] + d.box[2]) / 2 - cx) ** 2 +
                                      ((d.box[1] + d.box[3]) / 2 - cy) ** 2
                    )
                    tracked_objs.append({
                        "id":    int(tid),
                        "label": best.label.lower(),
                        "conf":  best.confidence,
                        "box":   best.box
                    })

            # 8) decide which one to drive the gimbal
            selected_label = (
                app_state.tracking_target.lower()
                if app_state.tracking_target else None
            )
            candidates = [
                o for o in tracked_objs
                if selected_label and o["label"] == selected_label
            ]
            # filter to only objects of that class
            if candidates:
                # pick lowest ID among matching class
                best = min(candidates, key=lambda o: o["id"])
                x1,y1,x2,y2 = best["box"]
                cx, cy = (x1+x2)//2, (y1+y2)//2
                app_state.latest_target_coords = (cx, cy)
                app_state.target_lock.set()
            else:
                app_state.target_lock.clear()

            # 9) (optional) timing log
            loop_ms = (time.perf_counter() - loop_start)*1e3
            fps = 1000.0/loop_ms if loop_ms>0 else float('inf')
            # logger.info(f"Infer {infer_ms:.1f}ms, loop {loop_ms:.1f}ms, FPS {fps:.1f}")
            # # Print confidence and box for each tracked object
            # for obj in tracked_objs:
            #     logger.info(f"Track ID {obj['id']}, Label: {obj['label']}, "
            #                 f"Confidence: {obj['conf']:.2f}, Box: {obj['box']}")

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
