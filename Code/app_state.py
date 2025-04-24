# app_state.py
from threading import Event, Lock
from flask_socketio import SocketIO
from typing import Optional

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
        self.auto_calibrating = False
        self.last_laser_pixel = None

# Singleton instance to import everywhere
app_state = AppState()
