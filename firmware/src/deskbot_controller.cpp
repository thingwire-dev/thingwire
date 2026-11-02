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
        OledEyes::clearText();
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

/// True for the printable ASCII range U+0020 to U+007E
static bool _printable(const char* s) {
    for (; *s; s++) {
        if (*s < 0x20 || *s > 0x7E) {
            return false;
        }
    }
    return true;
}

static bool _blank(const char* s) {
    for (; *s; s++) {
        if (*s != ' ') {
            return false;
        }
    }
    return true;
}

/// Read an integer field (not a float, not a bool); false if absent or not an integer
static bool _int(JsonVariantConst value, const char* field, long* out) {
    JsonVariantConst v = value.is<JsonObjectConst>() ? value[field] : JsonVariantConst();
    if (!v.is<long>() || v.is<bool>()) {
        return false;
    }
    *out = v.as<long>();
    return true;
}

/// show_text: the device re-validates with compiled in limits and never trusts the gateway
static const char* _showText(JsonVariantConst value) {
    JsonVariantConst t = value.is<JsonObjectConst>() ? value["text"] : JsonVariantConst();
    if (!t.is<const char*>()) {
        return "text charset";
    }
    const char* text = t.as<const char*>();
    if (!_printable(text)) {
        return "text charset";
    }
    if (_blank(text)) {
        return "text blank";
    }
    if (strlen(text) > DESKBOT_TEXT_MAX_CHARS) {
        return "text too long";
    }
    long ms = DESKBOT_TEXT_DEFAULT_MS;
    if (!value["duration_ms"].isNull() &&
        (!_int(value, "duration_ms", &ms) || ms < 1 || ms > (long)DESKBOT_TEXT_MAX_MS)) {
        return "duration out of range";
    }
    OledEyes::showText(text, (unsigned long)ms);
    return nullptr;
}

static const char* _blink(JsonVariantConst value) {
    long times;
    if (!_int(value, "times", &times) || times < 1 || times > DESKBOT_BLINK_MAX_TIMES) {
        return "times out of range";
    }
    bool slow = false;
    if (!value["speed"].isNull()) {
        const char* speed = value["speed"].is<const char*>() ? value["speed"].as<const char*>() : "";
        if (strcmp(speed, "slow") == 0) {
            slow = true;
        } else if (strcmp(speed, "normal") != 0) {
            return "unknown speed";
        }
    }
    if (OledEyes::isTextVisible()) {
        return "display busy";
    }
    OledEyes::startBlink((int)times, slow);
    return nullptr;
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
        if (!_num(value, "speed_deg_s", &speed)) {
            return "sweep needs numeric speed_deg_s";
        }
        // Clamp to the TD range; ServoHead further limits to the per-axis slew.
        if (speed < DESKBOT_SWEEP_MIN_SPEED) {
            speed = DESKBOT_SWEEP_MIN_SPEED;
        } else if (speed > DESKBOT_SWEEP_MAX_SPEED) {
            speed = DESKBOT_SWEEP_MAX_SPEED;
        }
        _cancelSequence();
        _startSweep(speed);
        return nullptr;
    }
    if (strcmp(action, "show_text") == 0) {
        return _showText(value);
    }
    if (strcmp(action, "blink") == 0) {
        return _blink(value);
    }
    return "unknown action";
}

/// Display actions never refresh the deadman timer, so they cannot keep a head unparked
static bool _isDisplayAction(const char* action) {
    return strcmp(action, "show_text") == 0 || strcmp(action, "blink") == 0;
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
            if (!_isDisplayAction(target)) {
                _lastCommandMs = millis();
                _deadmanParked = false;
            }
        }
    }

    char* result = (char*)malloc(256);
    if (result) {
        serializeJson(ack, result, 256);
    }
    return result;
}

} // namespace DeskbotController
