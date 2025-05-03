import zmq
import time
import threading
import os
os.environ["USE_REMOTE_GIMBAL"] = "False"  # This is always set to False in this script
from motors import Motor1, Motor2, DEGREES_PER_STEP_1, DEGREES_PER_STEP_2, homing_procedure
from hardware import laser_pin, water_gun_pin, hall_sensor_1, hall_sensor_2
from app_state import app_state
import argparse
from dotenv import load_dotenv
load_dotenv(override=True)

context = zmq.Context()

GIMBAL_PORT = int(os.getenv("GIMBAL_PORT", 5555))
GIMBAL_SUB_PORT = int(os.getenv("GIMBAL_SUB_PORT", 5556))

# REP socket for receiving commands
rep_socket = context.socket(zmq.REP)
rep_socket.bind(f"tcp://0.0.0.0:{GIMBAL_PORT}")

# PUB socket for telemetry
pub_socket = context.socket(zmq.PUB)
pub_socket.bind(f"tcp://0.0.0.0:{GIMBAL_SUB_PORT}")

print("[Gimbal Server] Running homing procedure...")
app_state.homing_complete = homing_procedure()
print("[Gimbal Server] Homing complete." if app_state.homing_complete else "[Gimbal Server] Homing failed.")

def motor_run_loop():
    while True:
        Motor1.run()
        Motor2.run()
        time.sleep(0.001)

threading.Thread(target=motor_run_loop, daemon=True).start()

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
        print(f"[Gimbal Server] Received message: {message}")
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
