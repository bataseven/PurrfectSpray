import os
import threading
import time

USE_REMOTE_GIMBAL = os.getenv("USE_REMOTE_GIMBAL", "False") == "True"

if not USE_REMOTE_GIMBAL:
    from gpiozero import DigitalInputDevice, OutputDevice

    class TimedOutputDevice(OutputDevice):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

        def spray(self, duration=0.5):
            print(f"Spraying for {duration} seconds")
            def spray_task():
                self.on()
                time.sleep(duration)
                self.off()
            threading.Thread(target=spray_task, daemon=True).start()


    hall_sensor_1 = DigitalInputDevice(6, pull_up=False)
    hall_sensor_2 = DigitalInputDevice(26, pull_up=False)
    fan_pin = OutputDevice(11, active_high=True, initial_value=True)
    laser_pin = OutputDevice(0, active_high=True, initial_value=False)
    print("Water gun pin initialized as TimedOutputDevice")
    water_gun_pin = TimedOutputDevice(1, active_high=True, initial_value=False)

    enable_pin_1 = OutputDevice(12, active_high=False, initial_value=False)
    enable_pin_2 = OutputDevice(4, active_high=False, initial_value=False)

else:
    from gimbal_client import send_gimbal_command  # import safely from your ZMQ client

    class RemotePin:
        def __init__(self, name):
            self.name = name
            self.value = False

        def on(self):
            self.value = True
            send_gimbal_command({"cmd": self.name, "on": True})   
        
        def off(self):
            self.value = False
            send_gimbal_command({"cmd": self.name, "on": False})
            
        def spray(self, duration=0.5):
            if self.name == "spray":
                send_gimbal_command({"cmd": "spray", "duration": duration})

        def close(self):
            self.off()
        
    # Replace these names with the keys expected by your gimbal_server.py
    hall_sensor_1 = RemotePin("sensor1")  # if needed, or leave dummy
    hall_sensor_2 = RemotePin("sensor2")
    fan_pin = RemotePin("fan")  # optional, if used remotely
    laser_pin = RemotePin("laser")
    water_gun_pin = RemotePin("spray")  # special case below

    enable_pin_1 = RemotePin("enable1")
    enable_pin_2 = RemotePin("enable2")
