# app_state.py
from threading import Event, Lock
from flask_socketio import SocketIO
from typing import Optional
import threading

class AppState:
    def __init__(self):
        self.socketio: Optional[SocketIO] = None

        # Auto-tracking state
        self.auto_mode = False
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
        
        self.homing_complete = False
        self.homing_error = False
        self.auto_calibrating = False
        self.last_laser_pixel = None

        self.latest_slider_angles = None
        self.gimbal_cpu_temp = None
        
        self.motor1_deg = 0.0
        self.motor2_deg = 0.0
        self.laser_on = False
        self.sensor1_triggered = False
        self.sensor2_triggered = False
        
        self.water_gun_active = False
        
        self.shutdown_event = threading.Event()
        
        # ZMQ telemetry
        self.gimbal_status = {}
        
# Singleton instance to import everywhere
app_state = AppState()
