import cv2
import numpy as np
from picamera2 import Picamera2
from gpiozero import DigitalInputDevice, OutputDevice
from flask import Flask, Response, request, jsonify, render_template
import threading
import time
import io
import os
import logging
import signal
import sys
from AccelStepper import AccelStepper, DRIVER
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)

STEPS_PER_REV = 200  # Steps per revolution for the stepper motor
GEAR_RATIO_1 = 16/80  # Gear ratio for Motor 1
GEAR_RATIO_2 = 20/80  # Gear ratio for Motor 2
MICROSTEPS_1 = 1/4  # Microsteps for the stepper driver (Motor 1)
MICROSTEPS_2 = 1/4  # Microsteps for the stepper driver (Motor 2)

DEGREES_PER_STEP_1 = 360 / STEPS_PER_REV * MICROSTEPS_1 * GEAR_RATIO_1
DEGREES_PER_STEP_2 = 360 / STEPS_PER_REV * MICROSTEPS_2 * GEAR_RATIO_2

STEPPER_MAX_SPEED = 8000  # Max speed in steps per second
STEPPER_ACCELERATION = 20000  # Acceleration in steps per second^2

# GPIO setup for hall effect sensors
HALL_SENSOR_PIN_1 = 6  # For Motor 1
HALL_SENSOR_PIN_2 = 26  # For Motor 2
hall_sensor_1 = DigitalInputDevice(HALL_SENSOR_PIN_1, pull_up=False)
hall_sensor_2 = DigitalInputDevice(HALL_SENSOR_PIN_2, pull_up=False)

# Get the directory of the current script
script_dir = os.path.dirname(os.path.realpath(__file__))

# Paths to model files
model_weights = os.path.join(
    script_dir, "model", "mobilenet_iter_73000.caffemodel")
model_config = os.path.join(script_dir, "model", "deploy.prototxt")
class_labels = os.path.join(script_dir, "model", "labelmap_voc.prototxt")

# Initialize camera and model
picam2 = Picamera2()
picam2.configure(picam2.create_preview_configuration(
    main={"format": 'XRGB8888', "size": (640, 480)}))
net = cv2.dnn.readNetFromCaffe(model_config, model_weights)

fan_pin = OutputDevice(11, active_high=True, initial_value=True)
laser_pin = OutputDevice(0, active_high=True, initial_value=False)
water_gun_pin = OutputDevice(1, active_high=True, initial_value=False)

# Initialize stepper motors
Motor1 = AccelStepper(DRIVER, 19, 13, None, None, True)
Motor2 = AccelStepper(DRIVER, 18, 24, None, None, True)  # laser motor

enable_pin_1 = OutputDevice(12, active_high=False, initial_value=False)
enable_pin_2 = OutputDevice(4, active_high=False, initial_value=False)

# Configure motion parameters
Motor1.set_max_speed(1000)  # Slower speed for homing
Motor1.set_acceleration(1000)
Motor2.set_max_speed(1000)
Motor2.set_acceleration(1000)

# Global variables
latest_frame = None
frame_lock = threading.Lock()
frame_available = threading.Event()
motor_active = False
homing_complete = False


def home_motor(motor, hall_sensor, motor_num):
    """Robust homing procedure with proper sensor reading"""
    print(f"Homing Motor {motor_num}...")

    # Set slower speed for homing
    homing_speed = 500
    motor.set_speed(homing_speed)
    motor.set_acceleration(1000)

    # Function to actively read sensor with debounce
    def read_sensor():
        time.sleep(0.005)  # Small debounce delay
        # Returns True when magnet is detected (active LOW)
        return not hall_sensor.value

    # Initial sensor check
    if read_sensor():
        print(f"Motor {motor_num} sensor already triggered, moving away...")

        # Move backward until sensor is not triggered
        motor.set_speed(-homing_speed)
        while read_sensor():
            motor.run_speed()
            time.sleep(0.001)  # Small delay to prevent CPU overload

        print(f"Motor {motor_num} exited trigger zone")

    # Find start of trigger zone (forward)
    motor.set_speed(homing_speed)
    while not read_sensor():
        motor.run_speed()
        time.sleep(0.001)

    trigger_start = motor.current_position()
    print(f"Motor {motor_num} trigger start: {trigger_start}")

    # Find end of trigger zone (continue forward)
    while read_sensor():
        motor.run_speed()
        time.sleep(0.001)

    trigger_end = motor.current_position()
    print(f"Motor {motor_num} trigger end: {trigger_end}")

    # Calculate middle point
    middle_point = (trigger_start + trigger_end) // 2
    print(f"Motor {motor_num} middle point: {middle_point}")

    # Move to middle point and set as zero
    motor.move_to(middle_point)
    while motor.run():
        time.sleep(0.001)

    motor.set_current_position(0)
    print(f"Motor {motor_num} homing complete. New zero position set.")

def homing_procedure():
    """Perform homing for both motors"""
    global homing_complete

    # Home Motor 1
    home_motor(Motor1, hall_sensor_1, 1)

    # Home Motor 2
    home_motor(Motor2, hall_sensor_2, 2)

    # Reset motor parameters to operational values
    Motor1.set_max_speed(STEPPER_MAX_SPEED)
    Motor1.set_acceleration(STEPPER_ACCELERATION)
    Motor2.set_max_speed(STEPPER_MAX_SPEED)
    Motor2.set_acceleration(STEPPER_ACCELERATION)

    homing_complete = True
    print("Homing procedure complete for both motors")


def generate_frames():
    """Stream frames at up to 20 FPS, even if no new frame is available"""
    while True:
        got_frame = frame_available.wait(timeout=0.1)
        with frame_lock:
            if latest_frame is None:
                continue  # wait until a frame is available at least once
            ret, buffer = cv2.imencode('.jpg', latest_frame)
            frame = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        if got_frame:
            frame_available.clear()
        time.sleep(1 / 30.0)  # Cap FPS


# [Previous imports and setup remain the same until the index() function]


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/set_motor_position')
def set_motor_position():
    motor_num = request.args.get('motor', type=int)
    position_deg = request.args.get('position', type=float)

    if motor_num == 1:
        position_steps = int(position_deg / DEGREES_PER_STEP_1)
        Motor1.move_to(position_steps)
    elif motor_num == 2:
        position_steps = int(position_deg / DEGREES_PER_STEP_2)
        Motor2.move_to(position_steps)

    return jsonify({'status': f'Motor {motor_num} moving to {position_deg} degrees'})


@app.route('/get_motor_positions')
def get_motor_positions():
    motor1_deg = Motor1.current_position() * DEGREES_PER_STEP_1
    motor2_deg = Motor2.current_position() * DEGREES_PER_STEP_2
    return jsonify({
        'motor1': motor1_deg,
        'motor2': motor2_deg
    })

# Cpu temp
@app.route('/get_cpu_temp')
def get_cpu_temp():
    """Get CPU temperature"""
    temp = os.popen("vcgencmd measure_temp").readline()
    temp = temp.replace("temp=", "").replace("'C", "")
    return jsonify({'temp': temp})


# Add this global variable at the top with your other globals
water_gun_active = False

# Shoot water gun
@app.route('/shoot')
def shoot():
    """Activate water gun for 0.5 seconds without blocking, ignoring multiple calls"""
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

# Toggle laser
@app.route('/toggle_laser')
def toggle_laser():
    """Toggle laser on/off"""
    # Turn it on if it's off, and off if it's on
    if laser_pin.value:
        laser_pin.off()
        status = "Off"
    else:
        laser_pin.on()
        status = "On"
    return jsonify({'status': status})


# [Rest of your existing code remains unchanged]


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

# if cpu temp > 80, start fan, if <70 stop fan
def fan_loop():
    """Fan control loop based on CPU temperature"""
    while True:
        cpu_temp = os.popen("vcgencmd measure_temp").readline()
        cpu_temp = float(cpu_temp.replace("temp=", "").replace("'C", ""))
        if cpu_temp > 76:
            fan_pin.on()
        elif cpu_temp < 72:
            fan_pin.off()
        time.sleep(1)  # Check every second

def motor_loop():

    """Main motor control loop"""
    # First perform homing
    homing_procedure()

    # Then normal operation
    while True:
        if motor_active and homing_complete:
            # Example movement pattern
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


def capture_and_process():
    """Main capture and processing loop"""
    global latest_frame
    with open(class_labels, "r") as f:
        classes = f.read().strip().split("\n")

    picam2.start()

    try:
        while True:
            frame = picam2.capture_array()
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

            # Object detection
            blob = cv2.dnn.blobFromImage(frame, 0.007843, (300, 300), 127.5)
            net.setInput(blob)
            detections = net.forward()

            # Process detections (only cats)
            for i in range(detections.shape[2]):
                confidence = detections[0, 0, i, 2]
                if confidence > 0.5:
                    class_id = int(detections[0, 0, i, 1])
                    if class_id == 8:  # Cat
                        box = detections[0, 0, i, 3:7] * np.array(
                            [frame.shape[1], frame.shape[0], frame.shape[1], frame.shape[0]])
                        (startX, startY, endX, endY) = box.astype("int")
                        cv2.rectangle(frame, (startX, startY),
                                      (endX, endY), (0, 255, 0), 2)
                        cv2.putText(frame, classes[class_id], (startX, startY - 15),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

            with frame_lock:
                latest_frame = frame.copy()
                frame_available.set()

    finally:
        picam2.stop()
        hall_sensor_1.close()
        hall_sensor_2.close()
        fan_pin.off()
        Motor1.disable_outputs()
        Motor2.disable_outputs()


def graceful_exit(signum, frame):
    print("Shutting down cleanly...")
    fan_pin.off()
    laser_pin.off()
    water_gun_pin.off()
    Motor1.disable_outputs()
    Motor2.disable_outputs()
    hall_sensor_1.close()
    hall_sensor_2.close()
    sys.exit(0)

signal.signal(signal.SIGINT, graceful_exit)
signal.signal(signal.SIGTERM, graceful_exit)

if __name__ == '__main__':
    # Start the capture thread
    capture_thread = threading.Thread(target=capture_and_process)
    capture_thread.daemon = True
    capture_thread.start()

    # Start the motor control thread
    motor_thread = threading.Thread(target=motor_loop)
    motor_thread.daemon = True
    motor_thread.start()

    # Start the fan control thread
    fan_thread = threading.Thread(target=fan_loop)
    fan_thread.daemon = True
    fan_thread.start()

    # Start the Flask server
    app.run(host='0.0.0.0', port=5000, threaded=True, debug=False)
