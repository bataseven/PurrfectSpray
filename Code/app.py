import os
import time
import threading
import logging
import requests
from logging.handlers import RotatingFileHandler

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit

from camera import capture_and_process, frame_lock, latest_frame, detect_in_background, encode_loop, stream_frames_over_zmq
from motors import Motor1, Motor2, homing_procedure, DEGREES_PER_STEP_1, DEGREES_PER_STEP_2
from hardware import laser_pin, water_gun_pin, fan_pin, hall_sensor_1, hall_sensor_2
from app_utils import get_cpu_temp, register_shutdown
import joblib
import app_globals
from gevent import monkey
monkey.patch_all(subprocess=False, thread=False)

logger = logging.getLogger("App")
logger.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
logger.addHandler(console_handler)

file_handler = RotatingFileHandler("app.log", maxBytes=512*1024, backupCount=3)
file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
logger.addHandler(file_handler)

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')
app_globals.socketio = socketio

script_dir = os.path.dirname(os.path.realpath(__file__))
model_path = os.path.join(script_dir, 'model.pkl')
model = joblib.load(model_path)

pcs = set()

motor_active = False
homing_complete = False
water_gun_active = False

@app.route('/')
def index():
    return render_template('index.html')

@app.route("/offer", methods=["POST"])
def offer_proxy():
    try:
        webrtc_res = requests.post("http://localhost:8080/offer", json=request.get_json())
        return jsonify(webrtc_res.json()), webrtc_res.status_code
    except Exception as e:
        logger.exception("Failed to forward WebRTC offer")
        return jsonify({"error": "Failed to connect to WebRTC server"}), 500


@socketio.on('connect')
def on_connect():
    app_globals.viewer_count += 1
    app_globals.socketio.emit("viewer_count", {"count": app_globals.viewer_count})
    print("[SOCKET] Client connected")

    emit('motor_status', {
        'status': f'Tracking {app_globals.tracking_target.capitalize()}' if app_globals.auto_mode else 'Idle',
        'auto_mode': app_globals.auto_mode,
        'target': app_globals.tracking_target
    })

    emit('laser_status', {
        'status': 'On' if laser_pin.value else 'Off'
    })

@socketio.on('disconnect')
def on_disconnect():
    if app_globals.viewer_count > 0:
        app_globals.viewer_count -= 1
        app_globals.socketio.emit("viewer_count", {"count": app_globals.viewer_count})
    if request.sid == app_globals.active_controller_sid:
        print("[INFO] Releasing controller lock on disconnect")
        app_globals.active_controller_sid = None
        socketio.emit("controller_update", {"sid": None})

@socketio.on('click_target')
def handle_click_target(data):
    if app_globals.auto_mode or not homing_complete:
        return
    
    if app_globals.active_controller_sid is None:
        app_globals.active_controller_sid = request.sid
        socketio.emit("controller_update", {"sid": request.sid}) 
    elif app_globals.active_controller_sid != request.sid:
        return
    
    if not homing_complete:
        return
    x = data.get('x')
    y = data.get('y')
    if x is None or y is None:
        return
    theta1, theta2 = model.predict([[x, y]])[0]
    steps1 = int(theta1 / DEGREES_PER_STEP_1)
    steps2 = int(theta2 / DEGREES_PER_STEP_2)
    Motor1.move_to(steps1)
    Motor2.move_to(steps2)
    emit('movement_ack', {
        'theta1': round(theta1, 2),
        'theta2': round(theta2, 2),
        'steps1': steps1,
        'steps2': steps2,
        'status': 'moving'
    })

@socketio.on('set_motor_position')
def handle_set_motor_position(data):
    if not homing_complete:
        return
    motor_num = data.get('motor')
    position_deg = data.get('position')

    if motor_num == 1:
        current_deg = Motor1.current_position() * DEGREES_PER_STEP_1
        target_deg = position_deg
        delta = target_deg - current_deg
        if abs(delta) < 1:
            return  # skip tiny moves
        Motor1.move_to(int(target_deg / DEGREES_PER_STEP_1))

    elif motor_num == 2:
        current_deg = Motor2.current_position() * DEGREES_PER_STEP_2
        target_deg = position_deg
        delta = target_deg - current_deg
        if abs(delta) < 1:
            return
        Motor2.move_to(int(target_deg / DEGREES_PER_STEP_2))


@socketio.on('toggle_laser')
def handle_laser():
    if laser_pin.value:
        laser_pin.off()
        emit('laser_status', {'status': 'Off'})
    else:
        laser_pin.on()
        emit('laser_status', {'status': 'On'})

@socketio.on('shoot')
def handle_shoot():
    global water_gun_active
    if water_gun_active:
        emit('shoot_ack', {'status': 'busy'})
        return
    water_gun_active = True
    water_gun_pin.on()
    def off():
        global water_gun_active
        time.sleep(0.5)
        water_gun_pin.off()
        water_gun_active = False
    threading.Thread(target=off).start()
    emit('shoot_ack', {'status': 'fired'})

@socketio.on('motor_control')
def handle_motor_control(data):
    global motor_active

    action = data.get('action')
    target_class = data.get('target')

    if not homing_complete:
        # 🔁 Notify only the sender since system is not ready
        emit('motor_status', {
            'status': 'Not ready (homing in progress)',
            'auto_mode': False
        })
        return

    if action == 'start':
        motor_active = True
        app_globals.active_controller_sid = request.sid
        app_globals.auto_mode = True
        if target_class:
            app_globals.tracking_target = target_class

        # 🔁 Notify all clients about Auto Mode start
        socketio.emit('motor_status', {
            'status': f'Tracking {app_globals.tracking_target.capitalize()}',
            'auto_mode': True,
            'target': app_globals.tracking_target
        })

    elif action == 'stop':
        motor_active = False
        app_globals.auto_mode = False
        app_globals.active_controller_sid = None
        # 🔁 Notify all clients about Auto Mode stop
        socketio.emit('motor_status', {
            'status': 'Auto Mode Stopped',
            'auto_mode': False
        })

    else:
        emit('motor_status', {
            'status': 'Unknown command',
            'auto_mode': app_globals.auto_mode
        })



def motor_loop():
    global homing_complete

    try:
        logger.info("Starting homing procedure")
        homing_procedure()
        homing_complete = True
        logger.info("Homing complete")

        last_steps = (None, None)
        current_coords = None

        while True:
            if motor_active and homing_complete and app_globals.target_lock.is_set():
                app_globals.target_lock.clear()

                new_coords = app_globals.latest_target_coords
                if new_coords != (None, None):
                    current_coords = new_coords  # Snap to the latest target

            # Interpolate toward the current_coords if defined
            if motor_active and homing_complete and current_coords:
                x, y = current_coords
                theta1, theta2 = model.predict([[x, y]])[0]
                steps1 = int(theta1 / DEGREES_PER_STEP_1)
                steps2 = int(theta2 / DEGREES_PER_STEP_2)

                # Smooth movement: don't jump all the way
                if last_steps == (None, None):
                    last_steps = (Motor1.current_position(), Motor2.current_position())

                interp1 = int(last_steps[0] + (steps1 - last_steps[0]) * 0.1)
                interp2 = int(last_steps[1] + (steps2 - last_steps[1]) * 0.1)

                Motor1.move_to(interp1)
                Motor2.move_to(interp2)

                last_steps = (interp1, interp2)

            Motor1.run()
            Motor2.run()
            time.sleep(0.001)

    except Exception as e:
        logger.exception("Error in motor_loop")


    except Exception as e:
        logger.exception("Error in motor_loop")


def fan_loop():
    try:
        while True:
            cpu_temp = get_cpu_temp()
            if cpu_temp > 76:
                fan_pin.on()
            elif cpu_temp < 72:
                fan_pin.off()
            time.sleep(1)
    except Exception as e:
        logger.exception("Error in fan_loop")


def status_broadcast_loop():
    try:
        while True:
            socketio.emit('status_update', {
                'motor1': Motor1.current_position() * DEGREES_PER_STEP_1,
                'motor2': Motor2.current_position() * DEGREES_PER_STEP_2,
                'cpu_temp': get_cpu_temp(),
                'laser': laser_pin.value,
                'homing': homing_complete,
                'sensor1': not hall_sensor_1.value,
                'sensor2': not hall_sensor_2.value
            })
            time.sleep(0.75)
    except Exception as e:
        logger.exception("Error in status_broadcast_loop")


def start_background_threads():
    register_shutdown()
    threading.Thread(target=capture_and_process, daemon=True).start()
    threading.Thread(target=motor_loop, daemon=True).start()
    threading.Thread(target=fan_loop, daemon=True).start()
    threading.Thread(target=status_broadcast_loop, daemon=True).start()
    threading.Thread(target=detect_in_background, daemon=True).start()
    threading.Thread(target=encode_loop, daemon=True).start()
    threading.Thread(target=stream_frames_over_zmq, daemon=True).start()
    logger.info("Background threads started")

start_background_threads()

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, use_reloader=False)