# gimbal_interface.py
import zmq
import os
import threading
from app_state import app_state, MotorMode
import time

USE_REMOTE_GIMBAL = os.getenv("USE_REMOTE_GIMBAL", "False") == "True"
GIMBAL_HOST = os.getenv("GIMBAL_HOST", "127.0.0.1")
GIMBAL_PORT = int(os.getenv("GIMBAL_PORT", 5555))
GIMBAL_SUB_PORT = int(os.getenv("GIMBAL_SUB_PORT", 5556))


def send_gimbal_command(command: dict) -> dict:
    """
    Send a single REQ→REP command to the gimbal server with a 500 ms timeout.
    Returns the server reply, or an error dict on timeout/failure.
    """
    if not USE_REMOTE_GIMBAL:
        return {"error": "send_gimbal_command called in local mode"}

    context = zmq.Context.instance()
    sock : zmq.Socket = context.socket(zmq.REQ)
    sock.connect(f"tcp://{GIMBAL_HOST}:{GIMBAL_PORT}")
    # Fail fast if send or recv take >500 ms
    sock.setsockopt(zmq.SNDTIMEO, 500)
    sock.setsockopt(zmq.RCVTIMEO, 500)

    try:
        sock.send_json(command)
        poller = zmq.Poller()
        poller.register(sock, zmq.POLLIN)
        socks = dict(poller.poll(500))
        if socks.get(sock) == zmq.POLLIN:
            return sock.recv_json()
        else:
            return {"error": "gimbal server timeout"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        sock.close()

homing_done = False
def update_gimbal_status_from_telemetry(status : dict):
    global homing_done
    app_state.motor1_deg = status.get("motor1", 0.0)
    app_state.motor2_deg = status.get("motor2", 0.0)
    app_state.laser_on = status.get("laser", False)
    app_state.sensor1_triggered = status.get("sensor1", False)
    app_state.sensor2_triggered = status.get("sensor2", False)
    app_state.gimbal_cpu_temp = status.get("gimbal_cpu_temp", None)
    if not homing_done:
        app_state.current_mode = MotorMode(status.get("mode", MotorMode.UNKNOWN.value))
    homing_done = status.get("mode", False) == MotorMode.IDLE.value
        

LOST_THRESHOLD = 1.0

def listen_for_telemetry(callback):
    if not USE_REMOTE_GIMBAL:
        return

    context = zmq.Context()
    telemetry_socket = context.socket(zmq.SUB)
    telemetry_socket.connect(f"tcp://{GIMBAL_HOST}:{GIMBAL_SUB_PORT}")
    telemetry_socket.setsockopt_string(zmq.SUBSCRIBE, '')

    def _worker():
        last_heard = time.time()
        while not app_state.shutdown_event.is_set():
            try:
                if telemetry_socket.poll(timeout=100):  # 100 ms
                    message = telemetry_socket.recv_json()
                    last_heard = time.time()
                    # when we get real telemetry *before* HOMING_COMPLETE,
                    # callback() already knows to absorb modes until then
                    callback(message)
                else:
                    # no data this iteration
                    now = time.time()
                    # if we’ve gone too long without hearing, mark lost
                    if now - last_heard > LOST_THRESHOLD:
                        app_state.current_mode = MotorMode.GIMBAL_NOT_FOUND
                # loop again
            except Exception as e:
                if not app_state.shutdown_event.is_set():
                    print(f"[Telemetry Error] {e}")
                break

        telemetry_socket.close()
        context.term()

    threading.Thread(target=_worker, daemon=True).start()