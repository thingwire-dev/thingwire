#include "servo_head.h"
#include "config.h"
#include "deskbot_gen.h"
#include <Arduino.h>
#include <math.h>

namespace ServoHead {

struct AxisState {
    int pin;
    float minDeg;
    float maxDeg;
    float parkDeg;
    float maxSlew;
    float speedCap;
    float angle;
    float target;
};

static AxisState _axes[AXIS_COUNT] = {
    {DESKBOT_PIN_SERVO_PAN, DESKBOT_PAN_MIN_DEG, DESKBOT_PAN_MAX_DEG, DESKBOT_PAN_PARK_DEG,
     DESKBOT_PAN_MAX_SLEW, 0.0f, DESKBOT_PAN_PARK_DEG, DESKBOT_PAN_PARK_DEG},
    {DESKBOT_PIN_SERVO_TILT, DESKBOT_TILT_MIN_DEG, DESKBOT_TILT_MAX_DEG, DESKBOT_TILT_PARK_DEG,
     DESKBOT_TILT_MAX_SLEW, 0.0f, DESKBOT_TILT_PARK_DEG, DESKBOT_TILT_PARK_DEG},
};
static unsigned long _lastTickMs = 0;

static void _writeAngle(const AxisState& a) {
    // Angle 0 is the pulse midpoint; +-90 degrees reach the pulse limits
    float centerUs = (DESKBOT_PULSE_MIN_US + DESKBOT_PULSE_MAX_US) / 2.0f;
    float halfUs = (DESKBOT_PULSE_MAX_US - DESKBOT_PULSE_MIN_US) / 2.0f;
    float us = centerUs + a.angle * (halfUs / 90.0f);
    uint32_t maxDuty = (1UL << SERVO_PWM_RESOLUTION_BITS) - 1;
    uint32_t duty = (uint32_t)(us * SERVO_PWM_FREQ_HZ * maxDuty / 1000000.0f);
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    ledcWrite(a.pin, duty);
#else
    ledcWrite((int)(&a - _axes), duty);
#endif
}

void init() {
    for (int i = 0; i < AXIS_COUNT; i++) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
        if (!ledcAttach(_axes[i].pin, SERVO_PWM_FREQ_HZ, SERVO_PWM_RESOLUTION_BITS)) {
            Serial.printf("[Servo] LEDC attach failed on GPIO %d\n", _axes[i].pin);
            continue;
        }
#else
        ledcSetup(i, SERVO_PWM_FREQ_HZ, SERVO_PWM_RESOLUTION_BITS);
        ledcAttachPin(_axes[i].pin, i);
#endif
        _axes[i].angle = _axes[i].parkDeg;
        _axes[i].target = _axes[i].parkDeg;
        _writeAngle(_axes[i]);
    }
    _lastTickMs = millis();
    Serial.println("[Servo] Initialized, parked");
}

void tick() {
    unsigned long now = millis();
    unsigned long dtMs = now - _lastTickMs;
    if (dtMs < SERVO_TICK_INTERVAL_MS) {
        return;
    }
    _lastTickMs = now;
    for (int i = 0; i < AXIS_COUNT; i++) {
        AxisState& a = _axes[i];
        float limit = a.maxSlew;
        if (a.speedCap > 0.0f && a.speedCap < limit) {
            limit = a.speedCap;
        }
        float maxStep = limit * (float)dtMs / 1000.0f;
        float diff = a.target - a.angle;
        if (fabsf(diff) <= maxStep) {
            a.angle = a.target;
        } else {
            a.angle += (diff > 0.0f) ? maxStep : -maxStep;
        }
        _writeAngle(a);
    }
}

bool inRange(Axis axis, float deg) {
    return !isnan(deg) && deg >= _axes[axis].minDeg && deg <= _axes[axis].maxDeg;
}

bool setTarget(Axis axis, float deg) {
    if (!inRange(axis, deg)) {
        Serial.printf("[Servo] Rejected axis %d target %.1f (limits %.0f..%.0f)\n",
                      (int)axis, deg, _axes[axis].minDeg, _axes[axis].maxDeg);
        return false;
    }
    _axes[axis].target = deg;
    return true;
}

void setSpeedCap(Axis axis, float degPerSec) {
    _axes[axis].speedCap = degPerSec > 0.0f ? degPerSec : 0.0f;
}

float getAngle(Axis axis) { return _axes[axis].angle; }

float getTarget(Axis axis) { return _axes[axis].target; }

bool atTarget(Axis axis) {
    return fabsf(_axes[axis].target - _axes[axis].angle) <= SERVO_SETTLE_EPSILON_DEG;
}

void park() {
    for (int i = 0; i < AXIS_COUNT; i++) {
        _axes[i].speedCap = 0.0f;
        _axes[i].target = _axes[i].parkDeg;
    }
}

} // namespace ServoHead
