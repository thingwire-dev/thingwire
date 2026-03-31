#include "sensor_registry.h"
#include "config.h"
#include <Arduino.h>
#include <DHT.h>

#define PIR_READ_INTERVAL_MS 1000

namespace SensorRegistry {

static DHT _dht(DHT_PIN, DHT22);
static float _temperature = NAN;
static float _humidity = NAN;
static bool _motion = false;
static bool _validReading = false;
static unsigned long _lastDhtReadMs = 0;
static unsigned long _lastPirReadMs = 0;

void init() {
    _dht.begin();
    pinMode(PIR_PIN, INPUT);
    Serial.println("[Sensor] DHT22 and PIR initialized");
}

void tick() {
    unsigned long now = millis();

    // DHT22 read on telemetry interval
    if (now - _lastDhtReadMs >= TELEMETRY_INTERVAL_MS) {
        _lastDhtReadMs = now;

        float t = _dht.readTemperature();
        float h = _dht.readHumidity();

        if (isnan(t) || isnan(h)) {
            Serial.println("[Sensor] DHT22 read failed, keeping previous values");
        } else {
            _temperature = t;
            _humidity = h;
            _validReading = true;
        }
    }

    // PIR read every second
    if (now - _lastPirReadMs >= PIR_READ_INTERVAL_MS) {
        _lastPirReadMs = now;
        _motion = digitalRead(PIR_PIN) == HIGH;
    }

    // Log readings periodically (aligned with DHT interval)
    if (_validReading && (now - _lastDhtReadMs < 100)) {
        Serial.printf("[Sensor] Temp: %.1f°C, Humidity: %.1f%%, Motion: %s\n",
                      _temperature, _humidity, _motion ? "YES" : "NO");
    }
}

float getTemperature() { return _temperature; }
float getHumidity() { return _humidity; }
bool getMotion() { return _motion; }
bool hasValidReading() { return _validReading; }

} // namespace SensorRegistry
