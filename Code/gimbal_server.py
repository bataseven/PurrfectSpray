import os
import time
import threading
import zmq
from dotenv import load_dotenv
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv(override=True)

assert os.getenv("USE_REMOTE_GIMBAL", "False") == "True", "This script is intended to run when USE_REMOTE_GIMBAL is True."
# Always force remote gimbal mode
os.environ["USE_REMOTE_GIMBAL"] = "False"

from app_utils import graceful_exit, register_shutdown, get_cpu_temp
from motors import Motor1, Motor2, DEGREES_PER_STEP_1, DEGREES_PER_STEP_2, homing_procedure
from hardware import laser_pin, water_gun_pin, hall_sensor_1, hall_sensor_2, enable_pin_1, enable_pin_2
from app_state import app_state, GimbalState


def create_zmq_sockets():
    context = zmq.Context()
    gimbal_port = int(os.getenv("GIMBAL_PORT", 5555))
    gimbal_sub_port = int(os.getenv("GIMBAL_SUB_PORT", 5556))

    rep_socket = context.socket(zmq.REP)
    rep_socket.bind(f"tcp://0.0.0.0:{gimbal_port}")

    pub_socket = context.socket(zmq.PUB)
    pub_socket.bind(f"tcp://0.0.0.0:{gimbal_sub_port}")

    return rep_socket, pub_socket

def run_motor_loop():
    logger.info("[Gimbal Server] Running homing procedure...")
    homing_procedure()
    logger.info("[Gimbal Server] Homing complete." if app_state.gimbal_state == GimbalState.READY else "[Gimbal Server] Homing failed.")
    while not app_state.shutdown_event.is_set():
        try:
            if app_state.gimbal_state == GimbalState.READY:
                Motor1.run()
                Motor2.run()
            time.sleep(0.001)
        except Exception as e:
            logger.info(f"[Motor Loop Error] {e}")
            break

def publish_status_loop(pub_socket: zmq.Socket):
    while not app_state.shutdown_event.is_set():
        try:
            status = {
                "motor1": Motor1.current_position() * DEGREES_PER_STEP_1,
                "motor2": Motor2.current_position() * DEGREES_PER_STEP_2,
                "laser": laser_pin.value,
                "sensor1": not hall_sensor_1.value,
                "sensor2": not hall_sensor_2.value,
                "gimbal_cpu_temp": get_cpu_temp(),
                "gimbal_state": app_state.gimbal_state.value,
            }
            pub_socket.send_json(status)
            time.sleep(0.5)
        except Exception as e:
            logger.info(f"[Publish Status Loop Error] {e}")
            break

def handle_command(rep_socket: zmq.Socket):
    try:
        message: dict = rep_socket.recv_json()
        logger.info(f"[Gimbal Server] Received message: {message}")
        cmd = message.get("cmd")

        if cmd == "move":
            if not app_state.gimbal_state == GimbalState.READY:
                rep_socket.send_json({"error": "Gimbal not ready for move command"})
                return
            motor = message.get("motor")
            pos_deg = message.get("position", 0)
            steps = int(pos_deg / (DEGREES_PER_STEP_1 if motor == 1 else DEGREES_PER_STEP_2))
            (Motor1 if motor == 1 else Motor2).move_to(steps)
            rep_socket.send_json({"status": "ok"})

        elif cmd == "laser":
            laser_pin.on() if message.get("on") else laser_pin.off()
            rep_socket.send_json({"status": "ok"})

        elif cmd == "spray":
            if message.get("on"):
                water_gun_pin.spray(message.get("duration", 0.5))
            rep_socket.send_json({"status": "ok"})
            
        elif cmd == "status":
            rep_socket.send_json({
                "motor1": Motor1.current_position() * DEGREES_PER_STEP_1,
                "motor2": Motor2.current_position() * DEGREES_PER_STEP_2,
                "laser": laser_pin.value,
                "mode": app_state.gimbal_state.value,
                "sensor1": not hall_sensor_1.value,
                "sensor2": not hall_sensor_2.value
            })

        elif cmd == "enable1":
            if not app_state.gimbal_state==GimbalState.READY:
                rep_socket.send_json({"error": "Can't use enable function while the gimbal is not ready"})
                return
            enable_pin_1.off() if message.get("on") else enable_pin_1.on()
            rep_socket.send_json({"status": "ok"})
            
        elif cmd == "enable2":
            if not app_state.gimbal_state==GimbalState.READY:
                rep_socket.send_json({"error": "Can't use enable function while the gimbal is not ready"})
                return
            enable_pin_2.off() if message.get("on") else enable_pin_2.on()
            rep_socket.send_json({"status": "ok"})
            
            
        else:
            rep_socket.send_json({"error": "Unknown command"})

    except Exception as e:
        rep_socket.send_json({"error": str(e)})

def main():
    register_shutdown()
    rep_socket, pub_socket = create_zmq_sockets()

    # start motor and status threads
    threading.Thread(target=run_motor_loop, daemon=True).start()
    threading.Thread(target=publish_status_loop, args=(pub_socket,), daemon=True).start()

    # set up a poller to allow polling with timeout
    poller = zmq.Poller()
    poller.register(rep_socket, zmq.POLLIN)

    logger.info("[Gimbal Server] Listening on port 5555 for commands...")
    try:
        # loop until shutdown_event is set
        while not app_state.shutdown_event.is_set():
            socks = dict(poller.poll(timeout=10))  # wait up to 10 ms
            if socks.get(rep_socket) == zmq.POLLIN:
                handle_command(rep_socket)
    except KeyboardInterrupt:
        # fallback in case of manual interrupt
        pass
    finally:
        graceful_exit(None, None)

if __name__ == "__main__":
    main()
