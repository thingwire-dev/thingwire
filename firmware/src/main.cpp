#include <Arduino.h>
#include "config.h"

void setup() {
    Serial.begin(115200);
    delay(1000); // Allow serial to initialize

    Serial.printf("[ThingWire] Booting... Device ID: %s\n", DEVICE_ID);
    Serial.printf("[ThingWire] MQTT Broker: %s:%d\n", MQTT_BROKER, MQTT_PORT);

    // Initialize status LED
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);

    // TODO: 1C — WiFi manager init
    // TODO: 1D — Sensor registry init
    // TODO: 1E — MQTT client init
    // TODO: 1F — Actuator controller init
    // TODO: 1G — WoT TD generator init

    Serial.println("[ThingWire] Boot complete.");
}

void loop() {
    // Non-blocking loop — all modules use millis()-based timers
    // TODO: 1D — Sensor reading tick
    // TODO: 1E — MQTT client tick
}
