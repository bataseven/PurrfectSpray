import os
import time
import threading
import json
from flask import Flask, render_template, request, jsonify, Response
from hardware import laser_pin

# Set CALIBRATION_MODE early
os.environ["CALIBRATION_MODE"] = "1"

from motors import Motor1, Motor2, homing_procedure, DEGREES_PER_STEP_1, DEGREES_PER_STEP_2

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
        time.sleep(1 / 20.0)  # limit FPS
        
app = Flask(__name__)

homing_complete = False
theta1 = 0
theta2 = 0
calibration_file = "calibration.json"

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


@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/homing_status')
def homing_status():
    return jsonify({'complete': homing_complete})

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
    data = request.get_json()
    x = data.get('x')
    y = data.get('y')

    if x is None or y is None:
        return jsonify({'error': 'Missing pixel coords'}), 400

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

@app.route('/set_motor_position')
def set_motor_position():
    motor_num = request.args.get('motor', type=int)
    position_deg = request.args.get('position', type=float)

    if motor_num == 1:
        steps = int(position_deg / DEGREES_PER_STEP_1)
        Motor1.move_to(steps)
    elif motor_num == 2:
        steps = int(position_deg / DEGREES_PER_STEP_2)
        Motor2.move_to(steps)

    return jsonify({'status': f'Motor {motor_num} set to {position_deg} degrees'})


def motor_loop():
    """Continuously run motors to reach target positions."""
    while True:
        Motor1.run()
        Motor2.run()
        time.sleep(0.001)

if __name__ == '__main__':
    print("[INFO] Homing motors before calibration...")
    homing_procedure()
    homing_complete = True
    print("[INFO] Homing complete.")

    laser_pin.on()  # 🚨 TURN ON LASER HERE

    threading.Thread(target=capture_and_process, daemon=True).start()
    threading.Thread(target=motor_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=5050, debug=False)
