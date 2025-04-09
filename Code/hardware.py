# hardware.py
from gpiozero import DigitalInputDevice, OutputDevice

hall_sensor_1 = DigitalInputDevice(6, pull_up=False)
hall_sensor_2 = DigitalInputDevice(26, pull_up=False)
fan_pin = OutputDevice(11, active_high=True, initial_value=True)
laser_pin = OutputDevice(0, active_high=True, initial_value=False)
water_gun_pin = OutputDevice(1, active_high=True, initial_value=False)

enable_pin_1 = OutputDevice(12, active_high=False, initial_value=False)
enable_pin_2 = OutputDevice(4, active_high=False, initial_value=False)
