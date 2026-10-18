#include "deskbot_controller.h"
#include "config.h"
#include "deskbot_gen.h"
#include "oled_eyes.h"
#include "servo_head.h"
#include <Arduino.h>
#include <ArduinoJson.h>
#include <math.h>
#include <string.h>

namespace DeskbotController {

enum Sequence { SEQ_NONE, SEQ_NOD, SEQ_SWEEP };

static Sequence _seq = SEQ_NONE;
static int _stepsLeft = 0;       // remaining strokes (nod) or legs (sweep)
static float _nodBase = 0.0f;
static bool _nodDown = true;
static bool _sweepToMax = false;
static unsigned long _lastCommandMs = 0;
static bool _deadmanParked = false;

static void _startNod(int times) {
    _nodBase = ServoHead::getTarget(ServoHead::TILT);
    _stepsLeft = times * 2;
    _nodDown = true;
    _seq = SEQ_NOD;
}

static void _startSweep(float speed) {
    ServoHead::setSpeedCap(ServoHead::PAN, speed);
    _stepsLeft = DESKBOT_SWEEP_PASSES * 2;
    _sweepToMax = false;
    _seq = SEQ_SWEEP;
}

static void _cancelSequence() {
    _seq = SEQ_NONE;
    ServoHead::setSpeedCap(ServoHead::PAN, 0.0f);
}

static void _advanceNod() {
    if (!ServoHead::atTarget(ServoHead::TILT)) {
        return;
    }
    if (_stepsLeft <= 0) {
        _seq = SEQ_NONE;
        return;
    }
    float t = _nodDown ? _nodBase - DESKBOT_NOD_AMPLITUDE_DEG : _nodBase;
    if (t < DESKBOT_TILT_MIN_DEG) {
        t = DESKBOT_TILT_MIN_DEG;
    }
    ServoHead::setTarget(ServoHead::TILT, t);
    _nodDown = !_nodDown;
    _stepsLeft--;
}

static void _advanceSweep() {
    if (!ServoHead::atTarget(ServoHead::PAN)) {
        return;
    }
    if (_stepsLeft <= 0) {
        _cancelSequence();
        ServoHead::park();
        return;
    }
    ServoHead::setTarget(ServoHead::PAN, _sweepToMax ? DESKBOT_PAN_MAX_DEG : DESKBOT_PAN_MIN_DEG);
    _sweepToMax = !_sweepToMax;
    _stepsLeft--;
}

void init() {
    _seq = SEQ_NONE;
    _lastCommandMs = millis();
    _deadmanParked = false;
}

void tick() {
    unsigned long now = millis();
    if (!_deadmanParked && now - _lastCommandMs >= DESKBOT_DEADMAN_MS) {
        _cancelSequence();
        ServoHead::park();
        _deadmanParked = true;
        Serial.println("[Deskbot] Deadman timeout, parking head");
    }
    if (_seq == SEQ_NOD) {
        _advanceNod();
    } else if (_seq == SEQ_SWEEP) {
        _advanceSweep();
    }
}

bool isDeadmanParked() { return _deadmanParked; }

/// Read a number from value (object field or bare scalar); false if absent or not numeric
static bool _num(JsonVariantConst value, const char* field, float* out) {
    JsonVariantConst v = value.is<JsonObjectConst>() ? value[field] : value;
    if (!v.is<float>() || v.isNull()) {
        return false;
    }
    *out = v.as<float>();
    return !isnan(*out);
}

/// Execute one action; returns nullptr on success or an error message
static const char* _execute(const char* action, JsonVariantConst value) {
    if (strcmp(action, "look_at") == 0) {
        float pan, tilt;
        if (!_num(value, "pan", &pan) || !_num(value, "tilt", &tilt)) {
            return "look_at needs numeric pan and tilt";
        }
        if (!ServoHead::inRange(ServoHead::PAN, pan) || !ServoHead::inRange(ServoHead::TILT, tilt)) {
            return "angle out of range";
        }
        _cancelSequence();
        ServoHead::setTarget(ServoHead::PAN, pan);
        ServoHead::setTarget(ServoHead::TILT, tilt);
        return nullptr;
    }
    if (strcmp(action, "set_expression") == 0) {
        const char* name = value.is<JsonObjectConst>() ? value["expression"].as<const char*>()
                                                       : value.as<const char*>();
        OledEyes::Expression e;
        if (!OledEyes::parseExpression(name, &e)) {
            return "unknown expression";
        }
        OledEyes::setExpression(e);
        return nullptr;
    }
    if (strcmp(action, "nod") == 0) {
        float times;
        if (!_num(value, "times", &times) || times < DESKBOT_NOD_MIN || times > DESKBOT_NOD_MAX) {
            return "nod times out of range";
        }
        _cancelSequence();
        _startNod((int)times);
        return nullptr;
    }
    if (strcmp(action, "sweep") == 0) {
        float speed;
        if (!_num(value, "speed_deg_s", &speed) || speed < DESKBOT_SWEEP_MIN_SPEED
            || speed > DESKBOT_SWEEP_MAX_SPEED) {
            return "sweep speed out of range";
        }
        _cancelSequence();
        _startSweep(speed);
        return nullptr;
    }
    return "unknown action";
}

char* handleCommand(const char* payload) {
    JsonDocument doc;
    JsonDocument ack;
    DeserializationError err = deserializeJson(doc, payload);

    if (err) {
        Serial.printf("[Deskbot] Invalid command JSON: %s\n", err.c_str());
        ack["status"] = "error";
        ack["error"] = "Invalid JSON";
    } else {
        const char* actionId = doc["action_id"] | "unknown";
        const char* target = doc["target"] | "unknown";
        ack["action_id"] = actionId;
        ack["target"] = target;
        const char* error = _execute(target, doc["value"]);
        if (error) {
            Serial.printf("[Deskbot] %s rejected: %s\n", target, error);
            ack["status"] = "error";
            ack["error"] = error;
        } else {
            ack["status"] = "ok";
            _lastCommandMs = millis();
            _deadmanParked = false;
        }
    }

    char* result = (char*)malloc(256);
    if (result) {
        serializeJson(ack, result, 256);
    }
    return result;
}

} // namespace DeskbotController
