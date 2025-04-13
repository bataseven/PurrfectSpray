# detectors.py

import cv2
import numpy as np
import os
import torch

class Detection:
    def __init__(self, class_id, label, confidence, box):
        self.class_id = class_id
        self.label = label
        self.confidence = confidence
        self.box = box  # (startX, startY, endX, endY)

class BaseDetector:
    def detect(self, frame):
        raise NotImplementedError("Detector must implement detect()")

class MobileNetDetector(BaseDetector):
    def __init__(self):
        script_dir = os.path.dirname(os.path.realpath(__file__))
        weights = os.path.join(script_dir, "model", "mobilenet_iter_73000.caffemodel")
        config = os.path.join(script_dir, "model", "deploy.prototxt")
        labelmap = os.path.join(script_dir, "model", "labelmap_voc.prototxt")

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
                label = self.classes[class_id] if 0 < class_id < len(self.classes) else "unknown"
                box = detections[0, 0, i, 3:7] * np.array([w, h, w, h])
                startX, startY, endX, endY = box.astype("int")
                results.append(Detection(class_id, label, confidence, (startX, startY, endX, endY)))
        return results

class YoloV5Detector(BaseDetector):
    def __init__(self, model_name='yolov5s', conf_threshold=0.5):
        self.model = torch.hub.load('ultralytics/yolov5', model_name, trust_repo=True)
        self.model.conf = conf_threshold  # confidence threshold
        self.model.eval()

    def detect(self, frame):
        results = self.model(frame)
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
