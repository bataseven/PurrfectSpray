# detectors.py

import os
import cv2
import numpy as np
import torch
from openvino.runtime import Core

highlight_colors = [
    (255, 99, 71),     # Tomato
    (135, 206, 235),   # Sky Blue
    (124, 252, 0),     # Lawn Green
    (255, 165, 0),     # Orange
    (147, 112, 219),   # Medium Purple
    (255, 215, 0),     # Gold
    (0, 206, 209),     # Dark Turquoise
    (255, 20, 147),    # Deep Pink
    (70, 130, 180),    # Steel Blue
    (154, 205, 50),    # Yellow Green
    (255, 105, 180),   # Hot Pink
    (0, 191, 255),     # Deep Sky Blue
    (199, 21, 133),    # Medium Violet Red
    (240, 230, 140),   # Khaki
    (106, 90, 205),    # Slate Blue
    (127, 255, 212),   # Aquamarine
    (244, 164, 96),    # Sandy Brown
    (60, 179, 113),    # Medium Sea Green
    (255, 160, 122),   # Light Salmon
    (0, 250, 154),     # Medium Spring Green
    (218, 112, 214),   # Orchid
    (255, 0, 255),     # Magenta
    (0, 128, 128),     # Teal
    (255, 228, 181),   # Moccasin
    (0, 255, 127),     # Spring Green
    (255, 69, 0),      # Orange Red
    (255, 140, 0),     # Dark Orange
    (173, 255, 47),    # Green Yellow
    (205, 92, 92),     # Indian Red
    (64, 224, 208),    # Turquoise
]

class Detection:
    def __init__(self, class_id, label, confidence, box):
        self.class_id = class_id
        self.label = label
        self.confidence = confidence
        self.box = box  # (startX, startY, endX, endY)

class BaseDetector:
    def detect(self, frame):
        raise NotImplementedError("Detector must implement detect()")

def _get_tracker_builder(name: str):
    """
    Return a callable that creates a Tracker<name> instance.
    Looks in:
      1. cv2.legacy.Tracker<name>_create
      2. cv2.Tracker<name>_create
    Raises ImportError if neither exists.
    """
    legacy_ns = getattr(cv2, "legacy", None)
    legacy_factory = getattr(legacy_ns, f"Tracker{name}_create", None) if legacy_ns else None
    top_factory    = getattr(cv2, f"Tracker{name}_create", None)

    if callable(legacy_factory):
        return legacy_factory
    if callable(top_factory):
        return top_factory

    raise ImportError(
        f"Tracker{name}_create not found in cv2; "
        f"ensure opencv-contrib-python is installed."
    )

class ActiveObjectTracker:
    def __init__(self, tracker_type="CSRT", max_track_frames=10, max_misses=5):
        """
        tracker_type: "CSRT", "KCF", or "MOSSE"
        """
        self._make_tracker   = _get_tracker_builder(tracker_type)
        self.max_track_frames = max_track_frames
        self.max_misses       = max_misses

        self.tracker       = None
        self.track_frames  = 0
        self.miss_count    = 0
        self.last_box      = None  # (x, y, w, h)
        self.last_label    = None
        
    def clear(self):
        """Clear the tracker state."""
        self.tracker       = None
        self.track_frames  = 0
        self.miss_count    = 0
        self.last_box      = None
        self.last_label    = None

    def update(self, frame, detections, target_class, conf_thresh=0.3):
        """
        frame:      BGR image (numpy array)
        detections: list of dicts { 'bbox':[x1,y1,x2,y2], 'class':str, 'conf':float }
        target_class: string name of the class to track
        conf_thresh: float confidence cutoff for detector
        Returns the frame with a rectangle drawn if tracking, else the raw frame.
        """
        # 1) filter detector hits of the class
        hits = [d for d in detections
                if d['class'] == target_class and d['conf'] >= conf_thresh]

        if hits:
            # pick highest-confidence detection
            best = max(hits, key=lambda d: d['conf'])
            x1, y1, x2, y2 = best['bbox']
            w, h = x2 - x1, y2 - y1

            # init or re-init tracker
            self.tracker = self._make_tracker()
            self.tracker.init(frame, (x1, y1, w, h))

            # reset counters
            self.track_frames = 0
            self.miss_count   = 0
            self.last_box     = (x1, y1, w, h)
            self.last_label   = target_class

        elif self.tracker is not None and self.track_frames < self.max_track_frames:
            # 2) fallback to tracker
            ok, box = self.tracker.update(frame)
            if ok:
                self.last_box     = tuple(map(int, box))
                self.track_frames += 1
            else:
                # tracker lost it
                self.tracker = None

        elif self.last_box is not None and self.miss_count < self.max_misses:
            # 3) hold the last box
            self.miss_count += 1

        else:
            # 4) give up
            self.tracker  = None
            self.last_box = None

        # draw if we have a box
        if self.last_box:
            x, y, w, h = self.last_box
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.putText(frame, self.last_label, (x, y - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

        return frame

# ----------------------------
# MobileNet SSD (OpenCV DNN)
# ----------------------------
class MobileNetDetector(BaseDetector):
    def __init__(self):
        script_dir = os.path.dirname(os.path.realpath(__file__))
        weights = os.path.join(script_dir, "models",
                               "mobilenet_iter_73000.caffemodel")
        config = os.path.join(script_dir, "models", "deploy.prototxt")
        labelmap = os.path.join(script_dir, "models", "labelmap_voc.prototxt")

        self.net = cv2.dnn.readNetFromCaffe(config, weights)
        with open(labelmap, "r") as f:
            self.classes = f.read().strip().split("\n")

    def detect(self, frame):
        h, w = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(frame, 0.007843, (300, 300), 127.5)
        self.net.setInput(blob)
        detections = self.net.forward()

        results = []
        for i in range(detections.shape[2]):
            confidence = float(detections[0, 0, i, 2])
            if confidence > 0.5:
                class_id = int(detections[0, 0, i, 1])
                label = self.classes[class_id] if 0 < class_id < len(self.classes) else "unknown"
                box = (detections[0, 0, i, 3:7] * np.array([w, h, w, h])).astype("int")
                startX, startY, endX, endY = box
                results.append(Detection(class_id, label, confidence, (startX, startY, endX, endY)))
        return results

# ----------------------------
# YOLOv5 via PyTorch (torch.hub)
# ----------------------------
class YoloV5Detector(BaseDetector):
    def __init__(self, model_name='yolov5n', conf_threshold=0.5, size=640):
        self.size = size
        self.model = torch.hub.load('ultralytics/yolov5', model_name, trust_repo=True)
        self.model.conf = conf_threshold
        self.model.eval()

    def detect(self, frame):
        results = self.model(frame, size=self.size)
        detections = results.xyxy[0]  # x1, y1, x2, y2, conf, class
        labels = self.model.names
        output = []
        for *box, conf, class_id in detections.tolist():
            if conf < self.model.conf:
                continue
            x1, y1, x2, y2 = map(int, box)
            label = labels[int(class_id)]
            output.append(Detection(int(class_id), label, float(conf), (x1, y1, x2, y2)))
        return output

# ----------------------------
# YOLOv5 via OpenVINO (IR format) — new class with NMS
# ----------------------------
class YoloV5VinoDetector(BaseDetector):
    def __init__(self,
                 xml_path: str,
                 conf_threshold: float = 0.5,
                 nms_threshold: float = 0.45):
        """
        xml_path:       Path to your yolov5n.xml (IR model)
        conf_threshold: confidence cutoff (0–1)
        nms_threshold:  IoU threshold for NMS (0–1)
        """
        self.conf_threshold = conf_threshold
        self.nms_threshold  = nms_threshold

        # 1) Compile the IR model for CPU
        self.core = Core()
        self.model = self.core.compile_model(xml_path, device_name="CPU")

        # 2) Ports & shapes
        self.input_port  = self.model.input(0)
        self.output_port = self.model.output(0)
        self.input_size  = tuple(self.input_port.shape[2:][::-1])  # (W, H)

        # 3) COCO class names
        self.class_names = [
            "person","bicycle","car","motorcycle","airplane","bus","train","truck","boat",
            "traffic light","fire hydrant","stop sign","parking meter","bench","bird","cat",
            "dog","horse","sheep","cow","elephant","bear","zebra","giraffe","backpack",
            "umbrella","handbag","tie","suitcase","frisbee","skis","snowboard","sports ball",
            "kite","baseball bat","baseball glove","skateboard","surfboard","tennis racket",
            "bottle","wine glass","cup","fork","knife","spoon","bowl","banana","apple",
            "sandwich","orange","broccoli","carrot","hot dog","pizza","donut","cake","chair",
            "couch","potted plant","bed","dining table","toilet","tv","laptop","mouse",
            "remote","keyboard","cell phone","microwave","oven","toaster","sink",
            "refrigerator","book","clock","vase","scissors","teddy bear","hair drier","toothbrush"
        ]

    def detect(self, frame: np.ndarray):
        orig_h, orig_w = frame.shape[:2]
        inp_w, inp_h   = self.input_size

        # resize & normalize into blob
        resized = cv2.resize(frame, (inp_w, inp_h))
        blob    = resized.astype(np.float32) / 255.0
        blob    = blob.transpose(2, 0, 1)[None, ...]

        # inference
        outputs = self.model([blob])[self.output_port]
        preds   = np.squeeze(outputs)  # shape: (num_preds, 85)

        # prepare for NMS
        raw_boxes    = []
        confidences  = []
        class_ids    = []

        scale_x = orig_w / inp_w
        scale_y = orig_h / inp_h

        for det in preds:
            conf = float(det[4])
            if conf < self.conf_threshold:
                continue

            # pick class
            scores = det[5:]
            cls_id = int(np.argmax(scores))

            # decode box
            xc, yc, bw, bh = det[:4]
            x1 = int((xc - bw/2) * scale_x)
            y1 = int((yc - bh/2) * scale_y)
            w  = int(bw * scale_x)
            h  = int(bh * scale_y)

            # clamp
            x1 = max(0, min(orig_w, x1))
            y1 = max(0, min(orig_h, y1))
            w  = max(1, min(orig_w - x1, w))
            h  = max(1, min(orig_h - y1, h))

            raw_boxes.append([x1, y1, w, h])
            confidences.append(conf)
            class_ids.append(cls_id)

        # apply NMS
        indices = cv2.dnn.NMSBoxes(raw_boxes, confidences,
                                   self.conf_threshold, self.nms_threshold)

        results = []
        if len(indices):
            for i in indices.flatten():
                x, y, w, h = raw_boxes[i]
                cls_id      = class_ids[i]
                label       = self.class_names[cls_id]
                conf        = confidences[i]
                results.append(
                    Detection(cls_id, label, conf, (x, y, x + w, y + h))
                )

        return results
