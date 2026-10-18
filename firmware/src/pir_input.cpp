#include "pir_input.h"
#include "config.h"
#include "deskbot_gen.h"
#include <Arduino.h>

namespace PirInput {

static bool _detected = false;
static bool _seenPending = false;
static unsigned long _lastMotionMs = 0;
static unsigned long _lastEventMs = 0;
static bool _haveEvent = false;

void init() {
    pinMode(DESKBOT_PIN_PIR, INPUT);
    Serial.printf("[PIR] Input on GPIO %d\n", DESKBOT_PIN_PIR);
}

void tick() {
    unsigned long now = millis();
    if (digitalRead(DESKBOT_PIN_PIR) == HIGH) {
        _lastMotionMs = now;
        if (!_detected) {
            _detected = true;
            if (!_haveEvent || now - _lastEventMs >= DESKBOT_PERSON_EVENT_GAP_MS) {
                _seenPending = true;
                _lastEventMs = now;
                _haveEvent = true;
            }
        }
    } else if (_detected && now - _lastMotionMs >= DESKBOT_PERSON_HOLD_MS) {
        _detected = false;
    }
}

bool personDetected() { return _detected; }

bool takePersonSeen() {
    bool pending = _seenPending;
    _seenPending = false;
    return pending;
}

} // namespace PirInput
