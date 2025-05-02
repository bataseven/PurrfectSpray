import zmq
import json
import time
import threading
from motors import Motor1, Motor2, DEGREES_PER_STEP_1, DEGREES_PER_STEP_2
from hardware import laser_pin, water_gun_pin, hall_sensor_1, hall_sensor_2
from app_state import app_state
import os
import argparse

# Parse --gpio flag
parser = argparse.ArgumentParser()
parser.add_argument("--gpio", action="store_true", help="Enable GPIO initialization")
args = parser.parse_args()

# Only set GPIO flag if remote gimbal is being used
USE_REMOTE_GIMBAL = os.getenv("USE_REMOTE_GIMBAL", "False") == "True"

if USE_REMOTE_GIMBAL and args.gpio:
    os.environ["GIMBAL_GPIO_ENABLED"] = "1"

context = zmq.Context()

# REP socket for receiving commands
rep_socket = context.socket(zmq.REP)
rep_socket.bind("tcp://0.0.0.0:5555")

# PUB socket for telemetry
pub_socket = context.socket(zmq.PUB)
pub_socket.bind("tcp://0.0.0.0:5556")

def publish_status_loop():
    while True:
        status = {
            "motor1": Motor1.current_position() * DEGREES_PER_STEP_1,
            "motor2": Motor2.current_position() * DEGREES_PER_STEP_2,
            "laser": laser_pin.value,
            "homing": app_state.homing_complete,
            "homing_error": app_state.homing_error,
            "sensor1": not hall_sensor_1.value,
            "sensor2": not hall_sensor_2.value
        }
        pub_socket.send_json(status)
        time.sleep(0.5)

threading.Thread(target=publish_status_loop, daemon=True).start()

print("[Gimbal Server] Listening on port 5555 for commands...")

while True:
    try:
        message = rep_socket.recv_json()
        cmd = message.get("cmd")

        if cmd == "move":
            motor = message.get("motor")
            pos_deg = message.get("position", 0)
            if motor == 1:
                steps = int(pos_deg / DEGREES_PER_STEP_1)
                Motor1.move_to(steps)
            elif motor == 2:
                steps = int(pos_deg / DEGREES_PER_STEP_2)
                Motor2.move_to(steps)
            rep_socket.send_json({"status": "ok"})

        elif cmd == "laser":
            laser_pin.on() if message.get("on") else laser_pin.off()
            rep_socket.send_json({"status": "ok"})

        elif cmd == "spray":
            water_gun_pin.on()
            time.sleep(0.5)
            water_gun_pin.off()
            rep_socket.send_json({"status": "ok"})

        elif cmd == "status":
            rep_socket.send_json({
                "motor1": Motor1.current_position() * DEGREES_PER_STEP_1,
                "motor2": Motor2.current_position() * DEGREES_PER_STEP_2,
                "laser": laser_pin.value,
                "homing": app_state.homing_complete,
                "homing_error": app_state.homing_error,
                "sensor1": not hall_sensor_1.value,
                "sensor2": not hall_sensor_2.value
            })

        else:
            rep_socket.send_json({"error": "Unknown command"})

    except Exception as e:
        rep_socket.send_json({"error": str(e)})
