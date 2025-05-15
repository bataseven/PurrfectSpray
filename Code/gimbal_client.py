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
_prev_mode            = None
_received_first_packet = False


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


def update_gimbal_status_from_telemetry(status: dict):
    """
    Maps 'mode'→MotorMode, treats IDLE as homing-done,
    and writes all the other fields into app_state.
    """
    global _received_first_packet

    # 1) Always update telemetry vars
    app_state.motor1_deg      = status.get("motor1", 0.0)
    app_state.motor2_deg      = status.get("motor2", 0.0)
    app_state.laser_on        = status.get("laser", False)
    app_state.sensor1_triggered = status.get("sensor1", False)
    app_state.sensor2_triggered = status.get("sensor2", False)
    app_state.gimbal_cpu_temp   = status.get("gimbal_cpu_temp", None)

    # 2) Turn the incoming string into our Enum
    mode_str = status.get("mode", GimbalState.UNKNOWN.value)
    try:
        incoming = GimbalState(mode_str)
    except ValueError:
        incoming = GimbalState.UNKNOWN

    # 3) First ever packet?  Trust it blindly and mark “connected”
    if not _received_first_packet:
        _received_first_packet = True
        app_state.gimbal_state = incoming

    # 4) Afterwards, only update if we’re not in a “disconnected” hold state
    elif app_state.gimbal_state != GimbalState.GIMBAL_NOT_FOUND:
        app_state.gimbal_state = incoming
    print(app_state.gimbal_state)


def listen_for_telemetry(callback):
    """
    Spawns a SUB→POLL→recv_json thread that:
     • on first packet      → clears GIMBAL_NOT_FOUND
     • on > threshold silence → flips into GIMBAL_NOT_FOUND
     • on return of packet   → restores previous mode & calls callback()
    """
    if not USE_REMOTE_GIMBAL:
        return

    ctx = zmq.Context()
    sock = ctx.socket(zmq.SUB)
    sock.connect(f"tcp://{GIMBAL_HOST}:{GIMBAL_SUB_PORT}")
    sock.setsockopt_string(zmq.SUBSCRIBE, "")

    def _worker():
        global _prev_mode
        last_heard = None

        while not app_state.shutdown_event.is_set():
            try:
                # wait up to 100 ms for a message
                if sock.poll(timeout=100):
                    msg = sock.recv_json()
                    now = time.time()

                    # 1st ever message or returning after a drop?
                    if not _received_first_packet:
                        # (recv flag set by callback)
                        pass
                    elif app_state.gimbal_state == GimbalState.GIMBAL_NOT_FOUND:
                        # restore previous mode
                        if _prev_mode is not None:
                            app_state.gimbal_state = _prev_mode
                            _prev_mode = None

                    last_heard = now
                    callback(msg)

                else:
                    # no data this cycle → have we connected once?
                    if last_heard is not None and (time.time() - last_heard) > _LOST_THRESHOLD:
                        # stash the *first* lost cause
                        if app_state.gimbal_state != GimbalState.GIMBAL_NOT_FOUND:
                            _prev_mode = app_state.gimbal_state
                            app_state.gimbal_state = GimbalState.GIMBAL_NOT_FOUND

                # loop around again

            except Exception as e:
                if not app_state.shutdown_event.is_set():
                    print(f"[Telemetry Error] {e}")
                break

        sock.close()
        ctx.term()

    threading.Thread(target=_worker, daemon=True).start()
