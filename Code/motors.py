# motors.py
import time
from AccelStepper import AccelStepper, DRIVER
import os
from hardware import hall_sensor_1, hall_sensor_2
from app_state import app_state

# Constants
STEPS_PER_REV = 200
GEAR_RATIO_1 = 16 / 80
GEAR_RATIO_2 = 20 / 80
MICROSTEPS_1 = 1 / 4
MICROSTEPS_2 = 1 / 4
DEGREES_PER_STEP_1 = 360 / STEPS_PER_REV * MICROSTEPS_1 * GEAR_RATIO_1
DEGREES_PER_STEP_2 = 360 / STEPS_PER_REV * MICROSTEPS_2 * GEAR_RATIO_2
STEPPER_MAX_SPEED = 8000
STEPPER_ACCELERATION = 20000

# Stepper motors
Motor1 = AccelStepper(DRIVER, 19, 13, None, None, True)
Motor2 = AccelStepper(DRIVER, 18, 24, None, None, True)

# Initial config (homing speed)
Motor1.set_max_speed(1000)
Motor1.set_acceleration(1000)
Motor2.set_max_speed(1000)
Motor2.set_acceleration(1000)

def home_motor(motor, hall_sensor, motor_num):
    print(f"Homing Motor {motor_num}...")
    homing_speed = 500
    motor.set_speed(homing_speed)
    motor.set_acceleration(1000)

    if motor_num == 1:
        degrees_per_step = DEGREES_PER_STEP_1
    elif motor_num == 2:
        degrees_per_step = DEGREES_PER_STEP_2
    else:
        raise ValueError("Invalid motor number")


    max_steps = int(10 / degrees_per_step)
    initial_position = motor.current_position()
    traveled_steps = 0


    def read_sensor():
        time.sleep(0.005)
        return not hall_sensor.value
    
    # If sensor already triggered, move away
    if read_sensor():
        print(f"Motor {motor_num} sensor already triggered, moving away...")
        motor.set_speed(-homing_speed)
        while read_sensor():
            motor.run_speed()
            traveled_steps = abs(motor.current_position() - initial_position)
            if traveled_steps >= max_steps:
                print(f"[ERROR] Motor {motor_num} homing failed: Hall sensor not found within {max_steps * degrees_per_step} degrees.")
                raise RuntimeError(f"Motor {motor_num} homing failed")
            time.sleep(0.001)
        print(f"Motor {motor_num} exited trigger zone")

    # Start homing forward
    motor.set_speed(homing_speed)
    traveled_steps = 0

    while not read_sensor():
        motor.run_speed()
        traveled_steps = abs(motor.current_position() - initial_position)
        if traveled_steps >= max_steps:
            print(f"[ERROR] Motor {motor_num} homing failed: Hall sensor not found within {max_steps * degrees_per_step} degrees.")
            raise RuntimeError(f"Motor {motor_num} homing failed")

        time.sleep(0.001)

    print(f"Motor {motor_num} sensor detected after {traveled_steps} steps")

    trigger_start = motor.current_position()

    while read_sensor():
        motor.run_speed()
        time.sleep(0.001)

    trigger_end = motor.current_position()

    middle_point = (trigger_start + trigger_end) // 2
    motor.move_to(middle_point)

    while motor.run():
        time.sleep(0.001)

    motor.set_current_position(0)
    print(f"Motor {motor_num} homing complete. New zero position set.")


import logging
logger = logging.getLogger("App")  # reuse app logger if shared context

def homing_procedure():
    try:
        home_motor(Motor1, hall_sensor_1, 1)
        home_motor(Motor2, hall_sensor_2, 2)
        Motor1.set_max_speed(STEPPER_MAX_SPEED)
        Motor1.set_acceleration(STEPPER_ACCELERATION)
        Motor2.set_max_speed(STEPPER_MAX_SPEED)
        Motor2.set_acceleration(STEPPER_ACCELERATION)
        logger.info("Homing procedure complete and speed limits set")
        app_state.homing_error = False
        app_state.homing_complete = True
        return True
    except Exception as e:
        logger.exception("Error during homing_procedure")
        app_state.homing_error = True
        app_state.homing_complete = False
        return False
