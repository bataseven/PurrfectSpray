# utils.py
import os
import signal
import sys
from hardware import fan_pin, laser_pin, water_gun_pin, hall_sensor_1, hall_sensor_2
from motors import Motor1, Motor2

def get_cpu_temp():
    temp = os.popen("vcgencmd measure_temp").readline()
    return float(temp.replace("temp=", "").replace("'C", ""))

def graceful_exit(signum, frame):
    print("Shutting down cleanly...")
    fan_pin.off()
    laser_pin.off()
    water_gun_pin.off()
    Motor1.disable_outputs()
    Motor2.disable_outputs()
    hall_sensor_1.close()
    hall_sensor_2.close()
    sys.exit(0)

def register_shutdown():
    signal.signal(signal.SIGINT, graceful_exit)
    signal.signal(signal.SIGTERM, graceful_exit)
