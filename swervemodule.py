import ctre
import math


class SwerveModule:

    CIMCODER_COUNTS_PER_REV: int = 80
    WHEEL_DIAMETER: float = 0.0254 * 3
    DRIVE_ENCODER_GEAR_REDUCTION: float = 5.43956
    # The VEX Integrated encoders have 1 count per revolution, and there
    # is a 1:1 corrospondence to the angular position of the module.
    STEER_COUNTS_PER_RADIAN = 1.0 / math.tau

    drive_counts_per_rev = CIMCODER_COUNTS_PER_REV*DRIVE_ENCODER_GEAR_REDUCTION
    drive_counts_per_radian = drive_counts_per_rev / math.tau
    drive_counts_per_metre = drive_counts_per_rev / (math.pi * WHEEL_DIAMETER)

    # factor by which to scale velocities in m/s to give to our drive talon.
    # 0.1 is because SRX velocities are measured in ticks/100ms
    drive_velocity_to_native_units = drive_counts_per_metre*0.1

    def __init__(self, steer_talon: ctre.WPI_TalonSRX, drive_talon: ctre.WPI_TalonSRX,
                 steer_enc_offset: float, x_pos: float, y_pos: float,
                 drive_free_speed: float,
                 reverse_steer_direction: bool=True,
                 reverse_steer_encoder: bool=True,
                 reverse_drive_direction: bool=False,
                 reverse_drive_encoder: bool=False):

        self.steer_motor = steer_talon
        self.drive_motor = drive_talon
        self.x_pos = x_pos
        self.y_pos = y_pos
        self.steer_enc_offset = steer_enc_offset
        self.reverse_steer_direction = reverse_steer_direction
        self.reverse_steer_encoder = reverse_steer_encoder
        self.reverse_drive_direction = reverse_drive_direction
        self.reverse_drive_encoder = reverse_drive_encoder
        self.drive_free_speed = drive_free_speed

        self.absolute_rotation = False
        self.vx = 0
        self.vy = 0

        self.steer_motor.configSelectedFeedbackSensor(ctre.FeedbackDevice.CTRE_MagEncoder_Absolute)
        # changes sign of motor throttle vilues
        self.steer_motor.reverseOutput(self.reverse_steer_direction)
        # changes direction of motor encoder
        self.steer_motor.setInverted(self.reverse_steer_encoder)
        self.steer_motor.config_kP(1.0, 10)
        self.steer_motor.config_kI(0.0002, 10)
        self.steer_motor.config_kD(0.0, 10)
        self.reset_steer_setpoint()

        self.drive_motor.configSelectedFeedbackSensor(ctre.FeedbackDevice.QuadEncoder)
        # changes sign of motor throttle values
        self.drive_motor.reverseOutput(self.reverse_drive_direction)
        # changes direction of motor encoder
        self.drive_motor.setInverted(self.reverse_drive_encoder)
        self.steer_motor.config_kP(1.0, 10)
        self.steer_motor.config_kI(0.0, 10)
        self.steer_motor.config_kD(0.0, 10)
        self.steer_motor.config_kF(1024.0/self.drive_free_speed, 10)

        self.reset_encoder_delta()

    def set_rotation_mode(self, rotation_mode):
        """Set whether we want the modules to rotate to the nearest possible
        direction to get to the required angle (and sometimes face backwards),
        or to rotate fully forwards to the correct angle.
        :param rotation_mode: False to rotate to nearest possible, True to
        rotate forwards to the required angle."""
        self.absolute_rotation = rotation_mode

    def reset_steer_setpoint(self):
        """Reset the setpoint of the steer motor to its current position.

        This prevents the module unwinding on start.
        """
        self.steer_motor.set(ctre.ControlMode.Position, self.steer_motor.getSelectedSensorPosition())

    def reset_encoder_delta(self):
        """Re-zero the encoder deltas as returned from
        get_encoder_delta.
        This is intended to be called by the SwerveChassis in order to track
        odometry.
        """
        self.zero_azimuth = self.current_azimuth
        self.zero_drive_pos = (self.drive_motor.getSelectedSensorPosition()
                               / self.drive_counts_per_metre)

    def get_encoder_delta(self):
        """Return the difference between the modules' current position and
        their position at the last time reset_encoder_delta was called.
        This is intended to be called by the SwerveChassis in order to track
        odometry.
        """
        steer_delta = self.zero_azimuth - self.last_steer_pos
        drive_delta = self.zero_drive_pos - (self.drive_motor.getSelectedSensorPosition()
                                             / self.drive_counts_per_metre)
        return steer_delta, drive_delta

    def set_velocity(self, vx, vy):
        """Set the x and y components of the desired module velocity, relative
        to the robot.
        :param vx: desired x velocity, m/s (x is forward on the robot)
        :param vy: desired y velocity, m/s (y is left on the robot)
        """

        self.vx = vx
        self.vy = vy

        # calculate straight line velocity and angle of motion
        velocity = math.hypot(self.vx, self.vy)
        desired_azimuth = math.atan2(self.vy, self.vx)

        # if we have a really low velocity, don't do anything. This is to
        # prevent stuff like joystick whipping back and changing the module
        # azimuth
        if velocity < 0.05:
            return

        if self.absolute_rotation:
            # Calculate a delta to from the module's current setpoint (wrapped
            # to between +-pi), representing required rotation to get to our
            # desired angle
            delta = constrain_angle(desired_azimuth - self.current_azimuth)
        else:
            # figure out the most efficient way to get the module to the desired direction
            current_unwound_azimuth = constrain_angle(self.current_azimuth)
            delta = self.min_angular_displacement(current_unwound_azimuth, desired_azimuth)

        # Please note, this is *NOT WRAPPED* to +-pi, because if wrapped the module
        # will unwind
        azimuth_to_set = (self.current_azimuth+delta)
        # convert the direction to encoder counts to set as the closed-loop setpoint
        setpoint = (azimuth_to_set * self.STEER_COUNTS_PER_RADIAN
                    + self.steer_enc_offset)
        self.steer_motor.set(ctre.ControlMode.Position, setpoint)

        if not self.absolute_rotation:
            # logic to only move the modules when we are close to the corret angle
            azimuth_error = constrain_angle(self.current_azimuth - desired_azimuth)
            if abs(azimuth_error) < math.pi / 6.0:
                # if we are nearing the correct angle with the module forwards
                self.drive_motor.set(ctre.ControlMode.Velocity, velocity*self.drive_velocity_to_native_units)
            elif abs(azimuth_error) > math.pi - math.pi / 6.0:
                # if we are nearing the correct angle with the module backwards
                self.drive_motor.set(ctre.ControlMode.Velocity, -velocity*self.drive_velocity_to_native_units)
            else:
                self.drive_motor.set(ctre.ControlMode.Velocity, 0)
        else:
            self.drive_motor.set(ctre.ControlMode.Velocity, velocity*self.drive_velocity_to_native_units)

    @property
    def current_azimuth(self):
        """Return the current azimuth from the controller setpoint in radians."""
        setpoint = self.steer_motor.getClosedLoopTarget()
        return float(setpoint - self.steer_enc_offset) / self.STEER_COUNTS_PER_RADIAN

    @staticmethod
    def min_angular_displacement(current, target):
        """Return the minimum (signed) angular displacement to get from :param current:
        to :param target:. In radians."""
        target = constrain_angle(target)
        opp_target = constrain_angle(target + math.pi)
        current = constrain_angle(current)
        diff = constrain_angle(target - current)
        opp_diff = constrain_angle(opp_target - current)

        if abs(diff) < abs(opp_diff):
            return diff
        return opp_diff


def constrain_angle(angle):
    """Wrap :param angle: to between +pi and -pi"""
    return math.atan2(math.sin(angle), math.cos(angle))
