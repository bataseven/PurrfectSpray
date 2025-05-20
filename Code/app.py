from gevent import monkey # type: ignore
monkey.patch_all(subprocess=False, thread=False) # type: ignore
import os
from dotenv import load_dotenv
load_dotenv(override=True)
from flask_login import LoginManager, login_user, logout_user, login_required, UserMixin, current_user
from app_state import app_state, GimbalState, ControlMode
import joblib
from app_utils import get_cpu_temp, register_shutdown
from hardware import laser_pin, water_gun_pin, fan_pin, hall_sensor_1, hall_sensor_2, enable_pin_1, enable_pin_2
from motors import Motor1, Motor2, homing_procedure, DEGREES_PER_STEP_1, DEGREES_PER_STEP_2
from camera import capture_and_process, detect_in_background, stream_frames_over_zmq, set_detector
from flask_socketio import SocketIO, emit
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from logging.handlers import RotatingFileHandler
import requests
import logging
import threading
import time
import json
import cv2 
import numpy as np
from gimbal_client import listen_for_telemetry, update_gimbal_status_from_telemetry
from motors import request_home as local_request_home
from gimbal_client import request_home as remote_request_home


detector_name = "none"

logger = logging.getLogger("App")
logger.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(
    '%(asctime)s [%(levelname)s] %(message)s'))
logger.addHandler(console_handler)

file_handler = RotatingFileHandler("app.log", maxBytes=512*1024, backupCount=3)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s [%(levelname)s] %(message)s'))
logger.addHandler(file_handler)

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

script_dir = os.path.dirname(os.path.realpath(__file__))
models_path = os.path.join(script_dir, "models")

# Load surface polygons
with open(os.path.join(script_dir, 'surfaces.json'), 'r') as f:
    surfaces = json.load(f)

# Load model mapping
with open(os.path.join(models_path, 'model_mapping.json'), 'r') as f:
    model_mapping = json.load(f)

# Load all models into memory
surface_models = {}
for idx_str, model_filename in model_mapping.items():
    idx = int(idx_str)
    model_path = os.path.join(script_dir, model_filename)
    surface_models[idx] = joblib.load(model_path)

logger.info(f"Loaded {len(surface_models)} models.")

pcs = set()

motor_active = False
water_gun_active = False

# Load environment variables
SECRET_KEY = os.getenv("SECRET_KEY")
WEB_PASSWORD = os.getenv("WEB_PASSWORD")

app.secret_key = SECRET_KEY

# Setup login manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# Simple in-memory user model
class User(UserMixin):
    def __init__(self, id):
        self.id = id

# For Flask-Login to find the current user
@login_manager.user_loader
def load_user(user_id):
    return User(user_id)


WEB_PASSWORD = os.getenv("WEB_PASSWORD")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")


@app.route("/login", methods=["GET", "POST"])
def login():
    session.pop('_flashes', None)
    if request.method == "POST":
        password = request.form.get("password")
        remember = "remember" in request.form

        if password in [WEB_PASSWORD, ADMIN_PASSWORD]:
            user = User("admin")
            login_user(user, remember=remember)

            # ✅ Only set admin flag for admin password
            session["is_admin"] = (password == ADMIN_PASSWORD)

            flash("Login successful!", "success")
            return redirect(url_for("index"))
        else:
            flash("Incorrect password.", "danger")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    session.pop('_flashes', None)
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    is_admin = session.get("is_admin", False)
    return render_template("index.html", is_admin=is_admin)


@app.route("/offer", methods=["POST"])
def offer_proxy():
    try:
        webrtc_res = requests.post(
            "http://localhost:8080/offer", json=request.get_json())
        return jsonify(webrtc_res.json()), webrtc_res.status_code
    except Exception as e:
        logger.exception("Failed to forward WebRTC offer")
        return jsonify({"error": "Failed to connect to WebRTC server"}), 500


@socketio.on('connect')
def on_connect():
    enable_pin_1.on()
    enable_pin_2.on()
    if app_state.viewer_count == 0:
        # Set the detector to the default model if None is set
        if detector_name == "none" and app_state.control_mode != ControlMode.TRACKING:
            set_detector("openvino")
        if not laser_pin.value:
            threading.Thread(target=lambda: laser_pin.on(), daemon=True).start()
            socketio.emit('laser_status', {'status': 'On'})
    app_state.viewer_count += 1
    socketio.emit("viewer_count", {"count": app_state.viewer_count})
    logger.info(f"[SOCKET] Client connected (Count: {app_state.viewer_count})")

    emit("target_updated", { "target": app_state.tracking_target })
    
    emit('motor_status', {
        'control_mode': app_state.control_mode.value,
        'target': app_state.tracking_target
    })

    emit('laser_status', {
        'status': 'On' if laser_pin.value else 'Off'
    })


@socketio.on('disconnect')
def on_disconnect():
    if app_state.viewer_count > 0:
        app_state.viewer_count -= 1
        socketio.emit(
            "viewer_count", {"count": app_state.viewer_count})
    if request.sid == app_state.active_controller_sid:
        logger.info("[INFO] Releasing controller lock on disconnect")
        app_state.active_controller_sid = None
        socketio.emit("controller_update", {"sid": None})

    if app_state.viewer_count == 0:
        app_state.target_lock.clear()
        # If the mode is not tracking and no viewers are connected set the detector to None
        if app_state.control_mode != ControlMode.TRACKING:
            set_detector(None)
            app_state.tracking_target = None
            socketio.emit('model_changed', {'status': 'disabled'})
            # Disable the motors
            enable_pin_1.off()
            enable_pin_2.off()
        
        # Turn off the laser if no viewers are connected
        if laser_pin.value:
            threading.Thread(target=lambda: laser_pin.off(), daemon=True).start()
            socketio.emit('laser_status', {'status': 'Off'})
        

@socketio.on("request_home")
def handle_request_home():
    # always fire both; each module internally
    # no-ops if not applicable
    local_request_home()
    resp = remote_request_home()
    emit("home_ack", {"status": "started", "remote": resp})


@socketio.on('click_target')
def handle_click_target(data):
    if app_state.active_controller_sid is None:
        app_state.active_controller_sid = request.sid
        socketio.emit("controller_update", {"sid": request.sid})
    elif app_state.active_controller_sid != request.sid:
        return


    x = data.get('x')
    y = data.get('y')
    if x is None or y is None:
        return
    theta1, theta2 = predict_angles(x, y)
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
def handle_set_motor_position(data: dict):
    if not session.get("is_admin"):
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


@socketio.on('change_model')
def handle_change_model(data):
    model_name = data.get('model')
    global detector_name
    detector_name = model_name
    if model_name == "none":
        set_detector(None)
        emit('model_changed', {'status': 'disabled'})
        return

    try:
        set_detector(model_name)
        emit('model_changed', {'status': 'success', 'model': model_name})
    except Exception as e:
        emit('model_changed', {'status': 'error', 'message': str(e)})


@socketio.on('set_motor_mode')
def handle_motor_control(data):
    if not app_state.gimbal_state == GimbalState.READY:
        return
    
    global motor_active

    target_class = data.get('target')
    mode = data.get('mode')
    
    app_state.control_mode = ControlMode(mode) if mode else app_state.gimbal_state
    app_state.active_controller_sid = request.sid

    if mode == ControlMode.TRACKING.value:
        motor_active = True
        if target_class:
            app_state.tracking_target = target_class
        socketio.emit('motor_status', {
            'control_mode': app_state.control_mode.value,
            'target': app_state.tracking_target,
            'sid': app_state.active_controller_sid
        })

    elif mode == ControlMode.MANUAL.value:
        motor_active = False
        # Set the latest target to None
        app_state.latest_target_coords = (None, None)
        socketio.emit('motor_status', {
            'control_mode': app_state.control_mode.value,
            'sid': app_state.active_controller_sid
        })
        
    elif mode == ControlMode.FOLLOW.value:
        app_state.latest_target_coords = (None, None)
        motor_active = True
        socketio.emit('motor_status', {
            'control_mode': app_state.control_mode.value,
            'sid': app_state.active_controller_sid
        })
       
        
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
    if app_state.water_gun_active:
        emit('shoot_ack', {'status': 'busy'})
        return
    app_state.water_gun_active = True
    water_gun_pin.spray()
    # Reset flag after duration
    threading.Timer(0.5, lambda: setattr(app_state, 'water_gun_active', False)).start()
    emit('shoot_ack', {'status': 'fired'})


@socketio.on('update_target')
def handle_update_target(data):
    target = data.get("target")
    if target:
        app_state.tracking_target = target
        logger.info(f"[SocketIO] Target updated to: {target}")
        socketio.emit("target_updated", { "target": target })


def point_in_polygon(polygon, x, y):
    """Returns True if point (x, y) is inside polygon."""
    poly_np = np.array(polygon, np.int32)
    return cv2.pointPolygonTest(poly_np, (x, y), False) >= 0


def predict_angles(x, y):
    # 1. Find which surface the pixel belongs to
    for idx, surface in enumerate(surfaces):
        if point_in_polygon(surface["points"], x, y):
            model = surface_models[idx]
            theta1, theta2 = model.predict([[x, y]])[0]
            return theta1, theta2

    # 2. If no match, optionally: find closest polygon
    closest_idx = find_closest_surface(x, y)
    if closest_idx is not None:
        model = surface_models[closest_idx]
        theta1, theta2 = model.predict([[x, y]])[0]
        return theta1, theta2

    # 3. Otherwise, fallback
    raise ValueError("Pixel not inside any surface and no close polygon found.")


def find_closest_surface(x, y):
    min_dist = float('inf')
    closest_idx = None
    for idx, surface in enumerate(surfaces):
        center_x = np.mean([p[0] for p in surface["points"]])
        center_y = np.mean([p[1] for p in surface["points"]])
        dist = np.hypot(center_x - x, center_y - y)
        if dist < min_dist:
            min_dist = dist
            closest_idx = idx
    return closest_idx


def perform_interpolated_movement(current_coords, last_steps):
    x, y = current_coords
    theta1, theta2 = predict_angles(x, y)
    steps1 = int(theta1 / DEGREES_PER_STEP_1)
    steps2 = int(theta2 / DEGREES_PER_STEP_2)

    if last_steps == (None, None):
        last_steps = (Motor1.current_position(), Motor2.current_position())

    interp1 = int(last_steps[0] + (steps1 - last_steps[0]) * 0.1)
    interp2 = int(last_steps[1] + (steps2 - last_steps[1]) * 0.1)

    Motor1.move_to(interp1)
    Motor2.move_to(interp2)

    return (interp1, interp2)


def run_motor_loop():
    try:
        logger.info("Starting homing procedure")
        homing_procedure()

        last_steps = (None, None)
        current_coords = None

        while True:
            if motor_active and app_state.target_lock.is_set():
                app_state.target_lock.clear()
                new_coords = app_state.latest_target_coords
                if new_coords != (None, None):
                    current_coords = new_coords  # Snap to the latest target

            if motor_active and current_coords:
                last_steps = perform_interpolated_movement(current_coords, last_steps)

            if app_state.gimbal_state == GimbalState.READY:
                Motor1.run()
                Motor2.run()
            time.sleep(0.001)
    except Exception as e:
        logger.exception("Error in motor_loop")


def status_broadcast_loop():
    try:
        while True:
            socketio.emit('status_update', {
                'motor1': app_state.motor1_deg or 0.0,
                'motor2': app_state.motor2_deg or 0.0,
                'cpu_temp': get_cpu_temp(),
                'laser': app_state.laser_on,
                'control_mode': app_state.control_mode.value,
                'gimbal_state': app_state.gimbal_state.value,
                'sensor1': app_state.sensor1_triggered,
                'sensor2': app_state.sensor2_triggered,
                'gimbal_cpu_temp': app_state.gimbal_cpu_temp
            })
            socketio.sleep(0.5)
    except Exception as e:
        logger.exception("Error in status_broadcast_loop")


def start_local_gimbal_status_updater():
    if os.getenv("USE_REMOTE_GIMBAL", "False") == "True":
        return
    def _worker():
        while True:
            app_state.motor1_deg = Motor1.current_position() * DEGREES_PER_STEP_1
            app_state.motor2_deg = Motor2.current_position() * DEGREES_PER_STEP_2
            app_state.laser_on = laser_pin.value
            app_state.sensor1_triggered = not hall_sensor_1.value
            app_state.sensor2_triggered = not hall_sensor_2.value
            time.sleep(0.2)

    threading.Thread(target=_worker, daemon=True).start()


def start_background_threads():
    register_shutdown()
    threading.Thread(target=run_motor_loop, daemon=True).start()
    socketio.start_background_task(status_broadcast_loop)
    threading.Thread(target=capture_and_process, daemon=True).start()
    threading.Thread(target=detect_in_background, daemon=True).start()
    threading.Thread(target=stream_frames_over_zmq, daemon=True).start()
    listen_for_telemetry(lambda status: update_gimbal_status_from_telemetry(status))
    start_local_gimbal_status_updater()
    logger.info("Background threads started")



if __name__ == '__main__':
    start_background_threads()
    socketio.run(app, host='0.0.0.0', port=5000,
                 debug=True, use_reloader=False)
