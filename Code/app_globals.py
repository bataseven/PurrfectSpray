# app_globals.py
from threading import Event, Lock
from flask_socketio import SocketIO

socketio : SocketIO = None 

# Auto-tracking state
auto_mode = False
tracking_target = "person"

# Target for motor movement
latest_target_coords = (None, None)
target_lock = Event()

viewer_count = 0
# Lock for viewer count / controller access
viewer_lock = Lock()

jpeg_lock = Lock()
encoded_jpeg : bytes = None

# The client that is currently controlling the motors
active_controller_sid = None