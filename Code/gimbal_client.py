# gimbal_interface.py
import zmq
import os
import threading
from app_state import app_state

USE_REMOTE_GIMBAL = os.getenv("USE_REMOTE_GIMBAL", "False") == "True"
GIMBAL_HOST = os.getenv("GIMBAL_HOST", "127.0.0.1")
GIMBAL_PORT = int(os.getenv("GIMBAL_PORT", 5555))
GIMBAL_SUB_PORT = int(os.getenv("GIMBAL_SUB_PORT", 5556))

# -- Command socket
context = zmq.Context()
cmd_socket = context.socket(zmq.REQ)
cmd_socket.connect(f"tcp://{GIMBAL_HOST}:{GIMBAL_PORT}")

def send_gimbal_command(command: dict) -> dict:
    if not USE_REMOTE_GIMBAL:
        return {"error": "send_gimbal_command called in local mode"}
    try:
        cmd_socket.send_json(command)
        return cmd_socket.recv_json()
    except Exception as e:
        return {"error": str(e)}

def update_gimbal_status_from_telemetry(status):
    app_state.motor1_deg = status.get("motor1", 0.0)
    app_state.motor2_deg = status.get("motor2", 0.0)
    app_state.laser_on = status.get("laser", False)
    app_state.homing_complete = status.get("homing", False)
    app_state.homing_error = status.get("homing_error", False)
    app_state.sensor1_triggered = status.get("sensor1", False)
    app_state.sensor2_triggered = status.get("sensor2", False)

def listen_for_telemetry(callback):
    if not USE_REMOTE_GIMBAL:
        return

    context = zmq.Context()
    telemetry_socket = context.socket(zmq.SUB)
    telemetry_socket.connect(f"tcp://{GIMBAL_HOST}:{GIMBAL_SUB_PORT}")
    telemetry_socket.setsockopt_string(zmq.SUBSCRIBE, '')

    def _worker():
        while not app_state.shutdown_event.is_set():
            try:
                if telemetry_socket.poll(timeout=100):  # 100 ms timeout
                    message = telemetry_socket.recv_json()
                    callback(message)
            except Exception as e:
                if not app_state.shutdown_event.is_set():
                    print(f"[Telemetry Error] {e}")
                break

        telemetry_socket.close()
        context.term()

    threading.Thread(target=_worker, daemon=True).start()
