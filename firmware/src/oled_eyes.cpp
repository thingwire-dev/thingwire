#include "oled_eyes.h"
#include "config.h"
#include "deskbot_gen.h"
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Arduino.h>
#include <Wire.h>
#include <string.h>

namespace OledEyes {

// One frame per expression. Eye sizes are in pixels, lids are percent of eye height.
struct Frame {
    const char* name;
    uint8_t wL, hL, wR, hR;
    int8_t pupilDx, pupilDy;
    uint8_t pupilR;
    uint8_t lidTop;      // percent hidden from the top
    uint8_t lidBottom;   // percent hidden from the bottom
    int8_t slant;        // top lid slope in pixels, positive angers the inner corners
    uint8_t slowBlink;   // 1 doubles blink timings
};

static const Frame FRAMES[EXPRESSION_COUNT] = {
    {"neutral",   28, 34, 28, 34,  0,  0, 7,  0,  0,  0, 0},
    {"happy",     28, 30, 28, 30,  0,  2, 7,  0, 45,  0, 0},
    {"curious",   30, 38, 24, 28,  3, -4, 6,  0,  0,  0, 0},
    {"sleepy",    28, 30, 28, 30,  0,  4, 6, 55,  0,  0, 1},
    {"surprised", 34, 44, 34, 44,  0,  0, 4,  0,  0,  0, 0},
    {"alert",     30, 36, 30, 36,  0,  0, 7,  0,  0,  8, 0},
};

static Adafruit_SSD1306 _display(OLED_WIDTH, OLED_HEIGHT, &Wire, -1);
static bool _ready = false;
static bool _dirty = true;
static Expression _expr = NEUTRAL;
static unsigned long _lastFrameMs = 0;

enum BlinkPhase { BLINK_IDLE, BLINK_CLOSING, BLINK_OPENING };
static BlinkPhase _phase = BLINK_IDLE;
static unsigned long _phaseStartMs = 0;
static unsigned long _nextBlinkMs = 0;
static uint8_t _openness = 100;  // percent

static void _scheduleBlink(unsigned long now) {
    _nextBlinkMs = now + random(OLED_BLINK_MIN_MS, OLED_BLINK_MAX_MS);
}

static void _drawEye(int cx, int w, int h, const Frame& f, bool isLeft) {
    int eyeH = h * _openness / 100;
    if (eyeH < 2) {
        _display.drawFastHLine(cx - w / 2, OLED_EYE_CENTER_Y, w, SSD1306_WHITE);
        return;
    }
    int x = cx - w / 2;
    int y = OLED_EYE_CENTER_Y - eyeH / 2;
    _display.fillRoundRect(x, y, w, eyeH, w / 4, SSD1306_WHITE);
    if (_openness > 60) {
        _display.fillCircle(cx + f.pupilDx, OLED_EYE_CENTER_Y + f.pupilDy, f.pupilR, SSD1306_BLACK);
    }
    if (f.lidTop) {
        _display.fillRect(x, y, w, eyeH * f.lidTop / 100, SSD1306_BLACK);
    }
    if (f.lidBottom) {
        int cut = eyeH * f.lidBottom / 100;
        _display.fillRect(x, y + eyeH - cut, w, cut, SSD1306_BLACK);
    }
    if (f.slant) {
        // Triangle over the inner top corner (toward the nose)
        int inner = isLeft ? x + w : x;
        int outer = isLeft ? x : x + w;
        _display.fillTriangle(outer, y, inner, y, inner, y + f.slant, SSD1306_BLACK);
    }
}

static void _render() {
    const Frame& f = FRAMES[_expr];
    _display.clearDisplay();
    _drawEye(OLED_EYE_LEFT_X, f.wL, f.hL, f, true);
    _drawEye(OLED_EYE_RIGHT_X, f.wR, f.hR, f, false);
    _display.display();
}

void init() {
    Wire.begin(DESKBOT_PIN_I2C_SDA, DESKBOT_PIN_I2C_SCL, OLED_I2C_CLOCK_HZ);
    if (!_display.begin(SSD1306_SWITCHCAPVCC, OLED_I2C_ADDR)) {
        Serial.printf("[OLED] SSD1306 not found at 0x%02X, eyes disabled\n", OLED_I2C_ADDR);
        return;
    }
    _ready = true;
    _scheduleBlink(millis());
    Serial.println("[OLED] Initialized");
}

void tick() {
    if (!_ready) {
        return;
    }
    unsigned long now = millis();
    const Frame& f = FRAMES[_expr];
    unsigned long closeMs = OLED_BLINK_CLOSE_MS * (f.slowBlink ? 2 : 1);
    unsigned long openMs = OLED_BLINK_OPEN_MS * (f.slowBlink ? 2 : 1);
    uint8_t openness = _openness;

    switch (_phase) {
        case BLINK_IDLE:
            openness = 100;
            if ((long)(now - _nextBlinkMs) >= 0) {
                _phase = BLINK_CLOSING;
                _phaseStartMs = now;
            }
            break;
        case BLINK_CLOSING: {
            unsigned long t = now - _phaseStartMs;
            if (t >= closeMs) {
                openness = 0;
                _phase = BLINK_OPENING;
                _phaseStartMs = now;
            } else {
                openness = 100 - (uint8_t)(t * 100 / closeMs);
            }
            break;
        }
        case BLINK_OPENING: {
            unsigned long t = now - _phaseStartMs;
            if (t >= openMs) {
                openness = 100;
                _phase = BLINK_IDLE;
                _scheduleBlink(now);
            } else {
                openness = (uint8_t)(t * 100 / openMs);
            }
            break;
        }
    }

    if (openness != _openness) {
        _openness = openness;
        _dirty = true;
    }
    if (_dirty && (now - _lastFrameMs >= OLED_FRAME_INTERVAL_MS)) {
        _lastFrameMs = now;
        _dirty = false;
        _render();
    }
}

void setExpression(Expression e) {
    if (e >= EXPRESSION_COUNT) {
        return;
    }
    _expr = e;
    _dirty = true;
}

Expression getExpression() { return _expr; }

bool parseExpression(const char* name, Expression* out) {
    if (!name) {
        return false;
    }
    for (int i = 0; i < EXPRESSION_COUNT; i++) {
        if (strcmp(name, FRAMES[i].name) == 0) {
            *out = (Expression)i;
            return true;
        }
    }
    return false;
}

const char* expressionName(Expression e) {
    return e < EXPRESSION_COUNT ? FRAMES[e].name : "neutral";
}

} // namespace OledEyes
