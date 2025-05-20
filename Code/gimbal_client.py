# gimbal_interface.py

import os
import time
import threading
import zmq

from app_state import app_state, GimbalState


USE_REMOTE_GIMBAL = os.getenv("USE_REMOTE_GIMBAL", "False") == "True"
GIMBAL_HOST       = os.getenv("GIMBAL_HOST", "127.0.0.1")
GIMBAL_PORT       = int(os.getenv("GIMBAL_PORT",     5555))
GIMBAL_SUB_PORT   = int(os.getenv("GIMBAL_SUB_PORT", 5556))

# how long (s) w/o telemetry before we call it "disconnected"
_LOST_THRESHOLD = 1.0

# internal trackers:
_received_first_packet = False
_prev_gimbal_state    = None


def send_gimbal_command(command: dict) -> dict:
    """
    Send REQ→REP with a 500 ms send/recv timeout.
    """
    if not USE_REMOTE_GIMBAL:
        return {"error": "send_gimbal_command called in local mode"}

    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.REQ)
    sock.connect(f"tcp://{GIMBAL_HOST}:{GIMBAL_PORT}")
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


def request_home() -> dict:
    """
    In remote mode, send a 'home' command to the Gimbal Pi.
    In local mode, does nothing.
    """
    # send_gimbal_command already no-ops when USE_REMOTE_GIMBAL is False
    return send_gimbal_command({"cmd": "home"})



def update_gimbal_status_from_telemetry(status: dict):
    """
    Pulls all numeric/sensor fields into app_state,
    then maps the incoming 'mode' string into app_state.gimbal_state.
    """
    global _received_first_packet

    # 1) Raw telemetry always updates
    app_state.motor1_deg        = status.get("motor1", 0.0)
    app_state.motor2_deg        = status.get("motor2", 0.0)
    app_state.laser_on          = status.get("laser", False)
    app_state.sensor1_triggered = status.get("sensor1", False)
    app_state.sensor2_triggered = status.get("sensor2", False)
    app_state.gimbal_cpu_temp   = status.get("gimbal_cpu_temp", None)
    app_state.home_requested    = status.get("home_requested", False)

    # 2) Map the incoming string to your new GimbalState enum
    incoming_str = status.get("gimbal_state", None)
    try:
        incoming_state = GimbalState(incoming_str)
    except Exception:
        incoming_state = GimbalState.UNKNOWN

    # 3) On the very first packet, trust it and clear DISCONNECTED
    if not _received_first_packet:
        _received_first_packet = True
        app_state.gimbal_state = incoming_state

    # 4) On every subsequent packet, update the hardware state
    else:
        # any valid GimbalState will overwrite; 
        # DISCONNECTED is handled in listen_for_telemetry
        app_state.gimbal_state = incoming_state



def listen_for_telemetry(callback):
    """
    Spawns a thread that:
      • on >_LOST_THRESHOLD of silence ⇒ sets DISCONNECTED
      • on first packet or any packet after a drop ⇒ restores previous state,
        then calls callback(msg) to do update_gimbal_status_from_telemetry(msg).
    """
    if not USE_REMOTE_GIMBAL:
        return

    ctx = zmq.Context()
    sock = ctx.socket(zmq.SUB)
    sock.connect(f"tcp://{GIMBAL_HOST}:{GIMBAL_SUB_PORT}")
    sock.setsockopt_string(zmq.SUBSCRIBE, "")

    def _worker():
        global _prev_gimbal_state, _received_first_packet

        last_heard = None

        while not app_state.shutdown_event.is_set():
            try:
                if sock.poll(timeout=100):
                    msg = sock.recv_json()
                    now = time.time()

                    # 1st-ever or recovering from a drop?
                    if last_heard is None:
                        # first packet: will be handled by callback’s first-packet logic
                        pass
                    elif app_state.gimbal_state == GimbalState.GIMBAL_NOT_FOUND:
                        # restore the stashed hardware state
                        if _prev_gimbal_state is not None:
                            app_state.gimbal_state = _prev_gimbal_state
                            _prev_gimbal_state = None

                    # hand off to your updater
                    callback(msg)

                    last_heard = now

                else:
                    # no data: check for a dropout
                    if last_heard is not None and (time.time() - last_heard) > _LOST_THRESHOLD:
                        if app_state.gimbal_state != GimbalState.GIMBAL_NOT_FOUND:
                            _prev_gimbal_state = app_state.gimbal_state
                            app_state.gimbal_state = GimbalState.GIMBAL_NOT_FOUND

                # tiny yield
                time.sleep(0)

            except Exception as e:
                if not app_state.shutdown_event.is_set():
                    print(f"[Telemetry Error] {e}")
                break

        sock.close()
        ctx.term()

    threading.Thread(target=_worker, daemon=True).start()

