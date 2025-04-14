# export_openvino.py
from ultralytics import YOLO

# Load your model (smallest for RPi = yolov5n)
model = YOLO("yolov5n.pt")

# Export to OpenVINO IR format (generates .xml + .bin)
model.export(format="openvino", dynamic=False)
