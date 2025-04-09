import os
import time
import threading
import json
from flask import Flask, render_template, request, jsonify, Response
from hardware import laser_pin

# Set CALIBRATION_MODE early
os.environ["CALIBRATION_MODE"] = "1"

from camera import generate_frames, capture_and_process
from motors import Motor1, Motor2, homing_procedure, DEGREES_PER_STEP_1, DEGREES_PER_STEP_2

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
