# convert_to_ir.py
from pathlib import Path
from openvino.tools.ovc import convert_model

# Path to the ONNX you just exported
onnx_path = Path.home() / ".cache/torch/hub/ultralytics_yolov5_master" / "yolov5n.onnx"
# Desired output folder for the IR
out_dir = Path.home() / "my_project" / "openvino_model"

# Make sure the folder exists
out_dir.mkdir(parents=True, exist_ok=True)

# Run the conversion
convert_model(
    model=onnx_path.as_posix(),
    output_dir=out_dir.as_posix(),
    precision="FP16",             # compress to FP16
    input_shape=[1, 3, 640, 640]  # static input shape matching your ONNX
)
print(f"IR saved to {out_dir}")
