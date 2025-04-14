# app_state.py

from threading import Event

# Auto-tracking state
auto_mode = False
tracking_target = "person"

# Target for motor movement
latest_target_coords = (None, None)
target_lock = Event()
