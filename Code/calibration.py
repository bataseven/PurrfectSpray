import os
from dotenv import load_dotenv
load_dotenv(override=True)
import time
import threading
import json
from flask import Flask, render_template, request, jsonify, Response
from hardware import laser_pin
from app_state import app_state
# Set CALIBRATION_MODE early
os.environ["CALIBRATION_MODE"] = "1"

from motors import Motor1, Motor2, homing_procedure, DEGREES_PER_STEP_1, DEGREES_PER_STEP_2
from gimbal_client import listen_for_telemetry, update_gimbal_status_from_telemetry

import time
import cv2
from picamera2 import Picamera2
import numpy as np
from threading import Lock, Event
import logging

# Setup logger
logger = logging.getLogger("Camera")
logger.setLevel(logging.INFO)

# These should be defined at module level
frame_lock = Lock()
frame_available = Event()
latest_frame = None

app = Flask(__name__)

sweep_corners = []  # [(theta1, theta2), (theta1, theta2), ...]
theta1 = 0
theta2 = 0
calibration_file = "calibration.json"

# Camera init
try:
    picam2 = Picamera2()
    picam2.configure(picam2.create_preview_configuration(main={"format": 'XRGB8888', "size": (1920, 1080)}))
    picam2.start()
except Exception as e:
    logger.exception("Failed to initialize Picamera2")
    raise

def capture_and_process():
    global latest_frame
    fps = 0
    frame_count = 0
    last_fps_time = time.time()

    while True:
        try:
            # Capture and convert
            frame = picam2.capture_array()
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

            # Optional: draw FPS
            now = time.time()
            fps = 1 / (now - last_fps_time)
            last_fps_time = now
            cv2.putText(frame, f"FPS: {fps:.1f}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

            # Store frame
            with frame_lock:
                latest_frame = frame.copy()
                frame_available.set()

        except Exception as e:
            logger.exception("Exception in capture_and_process()")
            time.sleep(2)  # slow down if camera fails


def generate_frames():
    while True:
        with frame_lock:
            if latest_frame is None:
                time.sleep(0.01)
                continue
            frame = latest_frame.copy()

        # 🔍 Convert to HSV
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        # 🎯 Define red thresholds
        lower_red1 = np.array([0, 120, 200])
        upper_red1 = np.array([10, 255, 255])
        lower_red2 = np.array([160, 120, 200])
        upper_red2 = np.array([180, 255, 255])

        # 🟥 Get red mask
        mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
        mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
        red_mask = cv2.bitwise_or(mask1, mask2)

        # 🔧 Optional: apply dilation to make red blobs more visible
        red_mask = cv2.dilate(red_mask, None, iterations=2)

        green_overlay = cv2.merge([np.zeros_like(red_mask), red_mask, np.zeros_like(red_mask)])

        # 🧠 Overlay the mask with transparency
        overlayed = cv2.addWeighted(frame, 1.0, green_overlay, 1, 0)

        # 🧃 Encode and stream
        ret, buffer = cv2.imencode('.jpg', overlayed)
        if not ret:
            continue
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
        

@app.route('/')
def index():
    return render_template('calibration.html')

@app.route('/toggle_laser')
def toggle_laser():
    if laser_pin.value:
        laser_pin.off()
        status = "Off"
    else:
        laser_pin.on()
        status = "On"
    return jsonify({'status': status})


@app.route('/start_auto_calibration')
def start_auto_calibration():
    if not app_state.homing_complete:
        return jsonify({"message": "Please wait for homing to complete."}), 400

    if getattr(app_state, "auto_calibrating", False):
        return jsonify({"message": "Calibration is already running."}), 400

    def auto_calibration_worker():
        if len(sweep_corners) < 4:
            print("[ERROR] Not enough corners recorded!")
            return

        print("[INFO] Starting auto calibration...")
        app_state.auto_calibrating = True
        
        # Find bounding box
        thetas1 = [corner[0] for corner in sweep_corners]
        thetas2 = [corner[1] for corner in sweep_corners]
        
        theta1_min = min(thetas1)
        theta1_max = max(thetas1)
        theta2_min = min(thetas2)
        theta2_max = max(thetas2)
        
        theta_step = 5  # degrees

        # Load existing data if any
        filename = "calibration.json"
        if os.path.exists(filename):
            with open(filename, "r") as f:
                data = json.load(f)
        else:
            data = []

        for theta1 in np.arange(theta1_min, theta1_max + theta_step, theta_step):
            for theta2 in np.arange(theta2_min, theta2_max + theta_step, theta_step):

                move_motor_to_position(1, theta1)
                move_motor_to_position(2, theta2)
                app_state.latest_slider_angles = (theta1, theta2)
                time.sleep(2.0)  # allow time to settle

                if app_state.last_laser_pixel:
                    px, py = app_state.last_laser_pixel
                    entry = {
                        "pixel": [int(px), int(py)],
                        "angles": [round(theta1, 3), round(theta2, 3)]
                    }
                    data.append(entry)
                    print(f"Recorded: {entry}")

        with open(filename, "w") as f:
            json.dump(data, f, indent=2)

        print("[INFO] Auto calibration complete.")
        app_state.auto_calibrating = False

    threading.Thread(target=auto_calibration_worker, daemon=True).start()
    return jsonify({"message": "Auto calibration started."})

@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/homing_status')
def homing_status():
    return jsonify({'complete': app_state.homing_complete})

@app.route('/set_angles', methods=['POST'])
def set_angles():
    global theta1, theta2
    data = request.get_json()
    theta1 = data['theta1']
    theta2 = data['theta2']
    
    steps1 = int(theta1 / DEGREES_PER_STEP_1)
    steps2 = int(theta2 / DEGREES_PER_STEP_2)

    Motor1.move_to(steps1)
    Motor2.move_to(steps2)

    return jsonify({'status': 'ok'})

@app.route('/record_point', methods=['POST'])
def record_point():
   # grab the last‐detected laser dot
    pixel = app_state.last_laser_pixel
    if not pixel:
        return jsonify({'error': 'No laser dot detected'}), 400
    x, y = pixel
    # Read angles from actual motor positions
    angle1 = Motor1.current_position() * DEGREES_PER_STEP_1
    angle2 = Motor2.current_position() * DEGREES_PER_STEP_2

    point = {
        'pixel': [x, y],
        'angles': [angle1, angle2]
    }

    if os.path.exists(calibration_file):
        with open(calibration_file, 'r') as f:
            existing = json.load(f)
    else:
        existing = []

    existing.append(point)

    with open(calibration_file, 'w') as f:
        json.dump(existing, f, indent=2)

    return jsonify({'status': 'recorded', 'point': point})

@app.route('/update_slider')
def update_slider():
    theta1 = Motor1.current_position() * DEGREES_PER_STEP_1
    theta2 = Motor2.current_position() * DEGREES_PER_STEP_2
    return jsonify({'theta1': theta1, 'theta2': theta2})

def move_motor_to_position(motor_num, position_deg):
    if motor_num == 1:
        steps = int(position_deg / DEGREES_PER_STEP_1)
        Motor1.move_to(steps)
    elif motor_num == 2:
        steps = int(position_deg / DEGREES_PER_STEP_2)
        Motor2.move_to(steps)

@app.route('/set_motor_position')
def set_motor_position():
    motor_num = request.args.get('motor', type=int)
    position_deg = request.args.get('position', type=float)
    move_motor_to_position(motor_num, position_deg)
    return jsonify({'status': f'Motor {motor_num} set to {position_deg} degrees'})

@app.route('/record_sweep_corner', methods=['POST'])
def record_sweep_corner():
    theta1 = Motor1.current_position() * DEGREES_PER_STEP_1
    theta2 = Motor2.current_position() * DEGREES_PER_STEP_2
    sweep_corners.append((theta1, theta2))
    return jsonify({'status': 'corner recorded', 'corners': sweep_corners})

@app.route('/slider_status')
def slider_status():
    if hasattr(app_state, "latest_slider_angles") and app_state.latest_slider_angles is not None:
        theta1, theta2 = app_state.latest_slider_angles
    else:
        theta1, theta2 = 0, 0

    return jsonify({'theta1': theta1, 'theta2': theta2})


def detect_laser_dot():
    global latest_frame
    while True:
        with frame_lock:
            if latest_frame is None:
                time.sleep(0.05)
                continue
            frame = latest_frame.copy()

        # Convert to HSV and threshold red regions
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        # Red has two ranges in HSV
        lower_red1 = np.array([0, 120, 200])
        upper_red1 = np.array([10, 255, 255])
        lower_red2 = np.array([160, 120, 200])
        upper_red2 = np.array([180, 255, 255])

        mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
        mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
        mask = cv2.bitwise_or(mask1, mask2)

        # Find contours and get the largest red blob (laser)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            largest = max(contours, key=cv2.contourArea)
            M = cv2.moments(largest)
            if M["m00"] != 0:
                cx = int(M["m10"] / M["m00"])
                cy = int(M["m01"] / M["m00"])
                # print(f"[Laser] Detected red dot at ({cx}, {cy})")
                # You can now collect (cx, cy) with the current motor angles for calibration
                app_state.last_laser_pixel = (cx, cy)
        time.sleep(0.1)


def motor_loop():
    """Continuously run motors to reach target positions."""
    homing_procedure()
    print("[INFO] Homing motors before calibration...")
    app_state.homing_complete = True
    print("[INFO] Homing complete.")
    while True:
        Motor1.run()
        Motor2.run()
        time.sleep(0.001)

if __name__ == '__main__':
    laser_pin.on()  # 🚨 TURN ON LASER HERE
    listen_for_telemetry(lambda status: update_gimbal_status_from_telemetry(status))
    threading.Thread(target=detect_laser_dot, daemon=True).start()
    threading.Thread(target=capture_and_process, daemon=True).start()
    threading.Thread(target=motor_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=False)
