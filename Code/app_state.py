# app_state.py
from threading import Event, Lock
from flask_socketio import SocketIO
from typing import Optional
import threading
from enum import Enum

class GimbalState(Enum):
    HOMING = "homing"
    HOMING_ERROR = "homing_error"
    GIMBAL_NOT_FOUND = "gimbal_not_found"
    READY = "ready"
    UNKNOWN = "unknown"
    
class ControlMode(Enum):
    """What the user/app is asking the gimbal to do."""
    MANUAL           = "manual"          # user clicks on video to move
    FOLLOW           = "follow"          # cursor‐follow mode
    TRACKING         = "tracking"        # auto‐tracking a detected object

class AppState:
    def __init__(self):
        self.tracking_target = "person"

        # Target for motor movement
        self.latest_target_coords = (None, None)
        self.target_lock = Event()

        # Viewer tracking and control
        self.viewer_count = 0
        self.viewer_lock = Lock()
        self.active_controller_sid = None

        # JPEG stream encoding
        self.encoded_jpeg: Optional[bytes] = None
        self.jpeg_lock = Lock()
        
        self.auto_calibrating = False
        self.last_laser_pixel = None

        self.latest_slider_angles = None
        self.gimbal_cpu_temp = None
        
        self.motor1_deg = 0.0
        self.motor2_deg = 0.0
        self.laser_on = False
        self.sensor1_triggered = False
        self.sensor2_triggered = False
        self.gimbal_state = GimbalState.GIMBAL_NOT_FOUND
        self.control_mode = ControlMode.MANUAL
        self.motor_active = False
        
        self.water_gun_active = False
        
        self.shutdown_event = threading.Event()
        
        # ZMQ telemetry
        self.gimbal_status = {}
        
# Singleton instance to import everywhere
app_state = AppState()
