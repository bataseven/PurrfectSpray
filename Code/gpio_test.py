import gpiozero as GPIO
import time

# 11, 0, 1 is working
# 7, 8  is busy
laser_pin = GPIO.OutputDevice(0, active_high=True, initial_value=False)
fan_pin = GPIO.OutputDevice(11, active_high=True, initial_value=False)
water_gun_pin = GPIO.OutputDevice(1, active_high=True, initial_value=False)

def loop():
    fan_pin.on()
    laser_pin.on()

    # # # turn on one after another
    # print("Turning on laser")
    # laser_pin.on()
    # time.sleep(1)  # Wait for 1 second
    # print("Turning off laser")
    # laser_pin.off()
    # time.sleep(1)  # Wait for 1 second
    
    print("Turning on water gun")
    water_gun_pin.on()
    time.sleep(.4)  # Wait for 1 second
    print("Turning off water gun")
    water_gun_pin.off()
    time.sleep(3)  # Wait for 1 second
    
    # print("Turning on fan")
    # fan_pin.on()
    # time.sleep(2)  # Wait for 1 second
    # print("Turning off fan")
    # fan_pin.off()
    # time.sleep(2)  # Wait for 1 second
    pass

if __name__ == '__main__':
    try:
        while True:  # Arduino-style loop
            loop()
    except KeyboardInterrupt:
        print("\nStopping program...")
    finally:
        water_gun_pin.off()
        fan_pin.off()
        laser_pin.off()
        print("Water gun disabled")
        # Disable the water gun pin
