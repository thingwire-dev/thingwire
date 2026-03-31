#include <Arduino.h>
#include <ArduinoJson.h>
#include "config.h"
#include "wifi_manager.h"
#include "sensor_registry.h"
#include "mqtt_client.h"
#include "actuator_controller.h"
#include "wot_td_generator.h"

static bool _tdPublished = false;
static unsigned long _lastTelemetryMs = 0;

static void onCommand(const char* topic, const char* payload) {
    Serial.printf("[ThingWire] Command on %s: %s\n", topic, payload);
    char* ack = ActuatorController::handleCommand(payload);
    if (ack) {
        MqttClient::publishRetained("status", ack);
        free(ack);
    }
}

void setup() {
    Serial.begin(115200);
    delay(1000);

    Serial.printf("[ThingWire] Booting... Device ID: %s\n", DEVICE_ID);

    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);

    WiFiManager::init();
    SensorRegistry::init();
    ActuatorController::init();
    MqttClient::init(onCommand);

    Serial.println("[ThingWire] Boot complete.");
}

void loop() {
    WiFiManager::tick();
    SensorRegistry::tick();
    MqttClient::tick();

    // Publish WoT TD once connected
    if (!_tdPublished && MqttClient::isConnected()) {
        char* td = WotTdGenerator::generate();
        if (td) {
            MqttClient::publishRetained("td", td);
            Serial.println("[ThingWire] Published WoT TD");
            free(td);
        }
        _tdPublished = true;
    }

    // Publish telemetry on interval
    unsigned long now = millis();
    if (MqttClient::isConnected() && SensorRegistry::hasValidReading()
        && (now - _lastTelemetryMs >= TELEMETRY_INTERVAL_MS)) {
        _lastTelemetryMs = now;

        JsonDocument doc;
        doc["timestamp"] = millis();
        JsonObject readings = doc["readings"].to<JsonObject>();

        JsonObject temp = readings["temp1"].to<JsonObject>();
        temp["value"] = SensorRegistry::getTemperature();
        temp["unit"] = "celsius";

        JsonObject hum = readings["humidity1"].to<JsonObject>();
        hum["value"] = SensorRegistry::getHumidity();
        hum["unit"] = "percent";

        JsonObject motion = readings["motion1"].to<JsonObject>();
        motion["value"] = SensorRegistry::getMotion();
        motion["unit"] = "boolean";

        char buf[512];
        serializeJson(doc, buf, sizeof(buf));
        MqttClient::publishTelemetry(buf);
    }
}
