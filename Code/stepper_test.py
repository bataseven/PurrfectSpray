import gpiozero as GPIO
import time
from AccelStepper import AccelStepper, DRIVER

Motor1 = AccelStepper(DRIVER, 19, 13, None, None, True)


# Set the enable pin to low to enable the motor enable bin is 12
enable_pin = GPIO.OutputDevice(12, active_high=False, initial_value=False)

Motor2 = AccelStepper(DRIVER, 18, 24, None, None, True)
	# Configure motion parameters
Motor1.set_max_speed(3000)  # steps/second
Motor1.set_acceleration(3000)
Motor2.set_max_speed(3000)
Motor2.set_acceleration(3000)

def loop():
	# run()
	# Put your motor control logic here
	if Motor1.current_position() == 0:
		Motor1.move_to(200)
	if Motor2.current_position() == 0:
		Motor2.move_to(200)

	print("Motor2 is running: ", Motor2.is_running(), " Position: ", Motor2.current_position())
	print("Motor1 is running: ", Motor1.is_running(), " Position: ", Motor1.current_position())
	Motor1.run()
	Motor2.run()
	time.sleep(0.001)  # Prevent CPU overload

	if Motor1.current_position() == 200:
		Motor1.move_to(0)
	if Motor2.current_position() == 200:
		Motor2.move_to(0)


if __name__ == '__main__':
	try:
		while True:  # Arduino-style loop
			loop()
	except KeyboardInterrupt:
		print("\nStopping program...")
	finally:
		Motor1.disable_outputs()
		Motor2.disable_outputs()
		print("Motors disabled")

		
