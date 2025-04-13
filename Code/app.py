# app.py
import time
import threading
from flask import Flask, render_template, Response, request, jsonify
from camera import generate_frames, capture_and_process, frame_lock, frame_available, latest_frame
from motors import Motor1, Motor2, homing_procedure, DEGREES_PER_STEP_1, DEGREES_PER_STEP_2
from hardware import laser_pin, water_gun_pin, fan_pin, hall_sensor_1, hall_sensor_2
from utils import get_cpu_temp, register_shutdown
import joblib
import numpy as np
import os

app = Flask(__name__)

motor_active = False
homing_complete = False
water_gun_active = False

script_dir = os.path.dirname(os.path.realpath(__file__))
model_path = os.path.join(script_dir, 'model.pkl')
model = joblib.load(model_path)

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/set_motor_position')
def set_motor_position():
    if not homing_complete:
        return jsonify({'error': 'Homing not complete'}), 400
    motor_num = request.args.get('motor', type=int)
    position_deg = request.args.get('position', type=float)

    if motor_num == 1:
        position_steps = int(position_deg / DEGREES_PER_STEP_1)
        Motor1.move_to(position_steps)
    elif motor_num == 2:
        position_steps = int(position_deg / DEGREES_PER_STEP_2)
        Motor2.move_to(position_steps)

    return jsonify({'status': f'Motor {motor_num} moving to {position_deg} degrees'})


@app.route('/click_target', methods=['POST'])
def click_target():
    if not homing_complete:
        return jsonify({'error': 'Homing not complete'}), 400
    data = request.get_json()
    x = data.get('x')
    y = data.get('y')

    if x is None or y is None:
        return jsonify({'error': 'Missing pixel coordinates'}), 400

    # Predict angles using trained model
    theta1, theta2 = model.predict([[x, y]])[0]

    # Move motors to predicted positions
    steps1 = int(theta1 / DEGREES_PER_STEP_1)
    steps2 = int(theta2 / DEGREES_PER_STEP_2)

    Motor1.move_to(steps1)
    Motor2.move_to(steps2)

    return jsonify({
        'theta1': round(theta1, 2),
        'theta2': round(theta2, 2),
        'steps1': steps1,
        'steps2': steps2,
        'status': 'moving'
    })


@app.route('/get_motor_positions')
def get_motor_positions():
    motor1_deg = Motor1.current_position() * DEGREES_PER_STEP_1
    motor2_deg = Motor2.current_position() * DEGREES_PER_STEP_2
    return jsonify({'motor1': motor1_deg, 'motor2': motor2_deg})


@app.route('/get_cpu_temp')
def get_cpu_temp_route():
    return jsonify({'temp': get_cpu_temp()})


@app.route('/shoot')
def shoot():
    global water_gun_active
    if water_gun_active:
        return jsonify({'status': 'busy'}), 200

    water_gun_active = True
    water_gun_pin.on()

    def turn_off():
        global water_gun_active
        time.sleep(0.5)
        water_gun_pin.off()
        water_gun_active = False

    threading.Thread(target=turn_off).start()
    return jsonify({'status': 'fired'}), 200


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


@app.route('/sensor_status/<int:sensor_num>')
def sensor_status(sensor_num):
    if sensor_num == 1:
        return "Detected!" if not hall_sensor_1.value else "Not detected"
    else:
        return "Detected!" if not hall_sensor_2.value else "Not detected"


@app.route('/homing_status')
def homing_status():
    return jsonify({'complete': homing_complete})


@app.route('/motor_control')
def motor_control():
    global motor_active
    action = request.args.get('action', '')

    if action == 'start' and homing_complete:
        motor_active = True
        return jsonify({'status': 'Running'})
    elif action == 'stop':
        motor_active = False
        Motor1.stop()
        Motor2.stop()
        return jsonify({'status': 'Stopped'})
    else:
        return jsonify({'status': 'Not ready' if not homing_complete else 'Unknown command'})


def motor_loop():
    global homing_complete
    homing_procedure()
    homing_complete = True
    while True:
        if motor_active and homing_complete:
            if abs(Motor1.current_position()) < 100:
                Motor1.move(1000)
            else:
                Motor1.move(-1000)
            if abs(Motor2.current_position()) < 200:
                Motor2.move(2000)
            else:
                Motor2.move(-2000)
        Motor1.run()
        Motor2.run()
        time.sleep(0.001)


def fan_loop():
    while True:
        cpu_temp = get_cpu_temp()
        if cpu_temp > 76:
            fan_pin.on()
        elif cpu_temp < 72:
            fan_pin.off()
        time.sleep(1)


def start_background_threads():
    register_shutdown()
    threading.Thread(target=capture_and_process, daemon=True).start()
    threading.Thread(target=motor_loop, daemon=True).start()
    threading.Thread(target=fan_loop, daemon=True).start()


start_background_threads()  # Run regardless of how app is launched

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True, debug=False)
