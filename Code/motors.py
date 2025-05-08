import os
import time
import math
import logging
from AccelStepper import AccelStepper, DRIVER
from hardware import hall_sensor_1, hall_sensor_2
from app_state import app_state
from gimbal_client import send_gimbal_command

logger = logging.getLogger("App")

# Constants
STEPS_PER_REV = 200
GEAR_RATIO_1 = 16 / 80
GEAR_RATIO_2 = 20 / 80
MICROSTEPS_1 = 1 / 4
MICROSTEPS_2 = 1 / 4
DEGREES_PER_STEP_1 = 360.0 / STEPS_PER_REV * MICROSTEPS_1 * GEAR_RATIO_1
DEGREES_PER_STEP_2 = 360.0 / STEPS_PER_REV * MICROSTEPS_2 * GEAR_RATIO_2
STEPPER_MAX_SPEED = 8000
STEPPER_ACCELERATION = 20000

# Angle normalization helpers

def normalize_angle(angle: float) -> float:
    """
    Wrap any angle (in degrees) into the range [-180, 180).
    e.g. 350 -> -10, -190 -> 170
    """
    return ((angle + 180) % 360) - 180


def closest_equivalent_angle(target: float, current: float) -> float:
    """
    Given a raw target angle and the current angle,
    return the equivalent-to-target (i.e. ±360*k)
    that lies closest to current.
    """
    tgt = normalize_angle(target)
    k = round((current - tgt) / 360.0)
    return tgt + 360.0 * k

# Remote vs Local mode
USE_REMOTE_GIMBAL = os.getenv("USE_REMOTE_GIMBAL", "False") == "True"
print(f"[Motors] Running with USE_REMOTE_GIMBAL={USE_REMOTE_GIMBAL}")

if USE_REMOTE_GIMBAL:
    class RemoteMotor:
        def __init__(self, motor_id: int, degrees_per_step: float):
            self.motor_id = motor_id
            self.degrees_per_step = degrees_per_step
            self._position = 0  # virtual position in steps

        def move_to(self, step_pos: int):
            # compute raw degrees and wrap to shortest path
            raw_deg = step_pos * self.degrees_per_step
            current_deg = self._position * self.degrees_per_step
            target_deg = closest_equivalent_angle(raw_deg, current_deg)
            new_steps = int(round(target_deg / self.degrees_per_step))
            self._position = new_steps
            send_gimbal_command({
                "cmd": "move",
                "motor": self.motor_id,
                "position": target_deg
            })

        def run(self):
            pass

        def set_speed(self, *_):
            pass

        def set_acceleration(self, *_):
            pass

        def current_position(self) -> int:
            return self._position

        def disable_outputs(self):
            pass

    Motor1 = RemoteMotor(1, DEGREES_PER_STEP_1)
    Motor2 = RemoteMotor(2, DEGREES_PER_STEP_2)

    def homing_procedure():
        print("[Remote] Skipping homing — expected to be done on Gimbal Pi.")
        app_state.homing_complete = True
        app_state.homing_error = False
        return True

else:
    class LocalMotor(AccelStepper):
        def __init__(self, interface, pin1, pin2, pin3, pin4, invert, degrees_per_step):
            super().__init__(interface, pin1, pin2, pin3, pin4, invert)
            self.degrees_per_step = degrees_per_step

        def move_to(self, step_pos: int):
            raw_deg = step_pos * self.degrees_per_step
            current_deg = self.current_position() * self.degrees_per_step
            target_deg = closest_equivalent_angle(raw_deg, current_deg)
            new_steps = int(round(target_deg / self.degrees_per_step))
            super().move_to(new_steps)

    Motor1 = LocalMotor(DRIVER, 19, 13, None, None, True, DEGREES_PER_STEP_1)
    Motor2 = LocalMotor(DRIVER, 18, 24, None, None, True, DEGREES_PER_STEP_2)

    Motor1.set_max_speed(STEPPER_MAX_SPEED)
    Motor1.set_acceleration(STEPPER_ACCELERATION)
    Motor2.set_max_speed(STEPPER_MAX_SPEED)
    Motor2.set_acceleration(STEPPER_ACCELERATION)

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
        except Exception:
            logger.exception("Error during homing_procedure")
            app_state.homing_error = True
            app_state.homing_complete = False
            return False


def home_motor(motor: AccelStepper, hall_sensor, motor_num: int):
    """
    Probe both directions up to ±180° and home toward the closer sensor.
    """
    print(f"Homing Motor {motor_num} toward nearest sensor edge.")
    homing_speed = 500
    motor.set_acceleration(1000)

    # select degrees_per_step
    if motor_num == 1:
        dps = DEGREES_PER_STEP_1
    elif motor_num == 2:
        dps = DEGREES_PER_STEP_2
    else:
        raise ValueError("Invalid motor number")

    max_steps = int(180.0 / dps)
    start_pos = motor.current_position()

    def sensor_active():
        time.sleep(0.005)
        return not hall_sensor.value

    # Probe forward (+)
    motor.set_speed(homing_speed)
    while not sensor_active() and abs(motor.current_position() - start_pos) < max_steps:
        motor.run_speed()
    fwd_steps = abs(motor.current_position() - start_pos)
    fwd_found = sensor_active()

    # return to start
    motor.move_to(start_pos)
    while motor.run():
        pass

    # Probe backward (-)
    motor.set_speed(-homing_speed)
    while not sensor_active() and abs(motor.current_position() - start_pos) < max_steps:
        motor.run_speed()
    bwd_steps = abs(motor.current_position() - start_pos)
    bwd_found = sensor_active()

    # Choose direction
    if fwd_found and (not bwd_found or fwd_steps <= bwd_steps):
        direction = 1
    elif bwd_found:
        direction = -1
    else:
        raise RuntimeError(f"Motor {motor_num}: sensor not found within ±180°")

    # Actual homing in chosen direction
    motor.set_speed(direction * homing_speed)
    # wait for trigger
    while not sensor_active(): motor.run_speed()
    trigger_start = motor.current_position()
    # wait for release
    while sensor_active(): motor.run_speed()
    trigger_end = motor.current_position()

    # center on midpoint
    midpoint = (trigger_start + trigger_end) // 2
    motor.move_to(midpoint)
    while motor.run(): pass

    motor.set_current_position(0)
    print(f"Motor {motor_num} homed at {'forward' if direction>0 else 'backward'} direction; zero set.")
