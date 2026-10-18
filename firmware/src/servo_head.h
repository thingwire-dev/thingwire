#ifndef SERVO_HEAD_H
#define SERVO_HEAD_H

namespace ServoHead {
    enum Axis { PAN = 0, TILT = 1, AXIS_COUNT = 2 };

    /// Attach LEDC PWM and drive both axes to the park pose
    void init();

    /// Call in loop() - advances each axis toward its target, honoring the slew limit
    void tick();

    /// True if the angle is inside the soft limits for the axis
    bool inRange(Axis axis, float deg);

    /// Set target angle; returns false (and ignores it) if outside the soft limits
    bool setTarget(Axis axis, float deg);

    /// Cap the speed for one axis (deg/s); never exceeds the axis maximum. 0 clears the cap
    void setSpeedCap(Axis axis, float degPerSec);

    /// Current (slewed) angle
    float getAngle(Axis axis);

    /// Current target angle
    float getTarget(Axis axis);

    /// True when the axis has reached its target
    bool atTarget(Axis axis);

    /// Command the park pose (slewed, not instant)
    void park();
}

#endif // SERVO_HEAD_H
