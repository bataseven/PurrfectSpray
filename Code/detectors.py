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
            confidence = detections[0, 0, i, 2]
            if confidence > 0.5:
                class_id = int(detections[0, 0, i, 1])
                label = self.classes[class_id] if 0 < class_id < len(
                    self.classes) else "unknown"
                box = detections[0, 0, i, 3:7] * np.array([w, h, w, h])
                startX, startY, endX, endY = box.astype("int")
                results.append(Detection(class_id, label,
                               confidence, (startX, startY, endX, endY)))
        return results


# ----------------------------
# YOLOv5 via PyTorch (torch.hub)
# ----------------------------
class YoloV5Detector(BaseDetector):
    def __init__(self, model_name='yolov5n', conf_threshold=0.5, size=640):
        self.size = size
        self.model = torch.hub.load(
            'ultralytics/yolov5', model_name, trust_repo=True)
        self.model.conf = conf_threshold  # confidence threshold
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
            output.append(Detection(int(class_id), label,
                          float(conf), (x1, y1, x2, y2)))
        return output


# ----------------------------
# YOLOv5 OpenVINO (IR format)
# ----------------------------
class YoloV5OVDetector(BaseDetector):
    def __init__(self, model_dir='yolov5nu_openvino_model', conf_threshold=0.5):
        self.conf_threshold = conf_threshold
        self.core = Core()
        self.model = self.core.compile_model(
            f"{model_dir}/yolov5nu.xml", "CPU")
        self.input_layer = self.model.input(0)
        self.output_layer = self.model.output(0)
        self.input_size = self.input_layer.shape[2:]  # [height, width]
        self.class_names = [
            "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
            "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
            "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
            "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
            "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
            "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
            "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
            "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
            "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator",
            "book", "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush"
        ]

    def detect(self, frame):
        detections = []
        try:
            h, w = frame.shape[:2]
            resized = cv2.resize(frame, tuple(self.input_size))
            blob = resized.transpose(2, 0, 1)[np.newaxis, :] / 255.0

            outputs = self.model([blob])[self.output_layer]
            outputs = np.squeeze(outputs)

            for det in outputs:
                conf = det[4]
                if conf < self.conf_threshold:
                    continue
                cls_id = int(np.argmax(det[5:]))
                label = self.class_names[cls_id] if cls_id < len(
                    self.class_names) else f"class_{cls_id}"

                x_center, y_center, width, height = det[:4]
                x_center *= w
                y_center *= h
                width *= w
                height *= h
                x1 = int(x_center - width / 2)
                y1 = int(y_center - height / 2)
                x2 = int(x_center + width / 2)
                y2 = int(y_center + height / 2)

                detections.append(
                    Detection(cls_id, label, float(conf), (x1, y1, x2, y2)))
        except Exception as e:
            import logging
            logging.getLogger("App").exception("Error in OpenVINO detect()")

        return detections
