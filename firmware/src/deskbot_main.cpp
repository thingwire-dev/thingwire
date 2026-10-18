#include <Arduino.h>
#include <ArduinoJson.h>
#include "config.h"
#include "deskbot_controller.h"
#include "deskbot_td.h"
#include "mqtt_client.h"
#include "oled_eyes.h"
#include "pir_input.h"
#include "servo_head.h"
#include "wifi_manager.h"

static bool _tdPublished = false;
static unsigned long _lastTelemetryMs = 0;

static void onCommand(const char* topic, const char* payload) {
    Serial.printf("[Deskbot] Command on %s: %s\n", topic, payload);
    char* ack = DeskbotController::handleCommand(payload);
    if (ack) {
        MqttClient::publishRetained("status", ack);
        free(ack);
    }
}

static void publishTelemetry() {
    JsonDocument doc;
    doc["timestamp"] = millis();
    JsonObject r = doc["readings"].to<JsonObject>();
    JsonObject pan = r["head_pan"].to<JsonObject>();
    pan["value"] = ServoHead::getAngle(ServoHead::PAN);
    pan["unit"] = "degree";
    JsonObject tilt = r["head_tilt"].to<JsonObject>();
    tilt["value"] = ServoHead::getAngle(ServoHead::TILT);
    tilt["unit"] = "degree";
    r["expression"]["value"] = OledEyes::expressionName(OledEyes::getExpression());
    JsonObject person = r["person_detected"].to<JsonObject>();
    person["value"] = PirInput::personDetected();
    person["unit"] = "boolean";
    char buf[512];
    serializeJson(doc, buf, sizeof(buf));
    MqttClient::publishTelemetry(buf);
}

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.printf("[ThingWire] Desk bot booting, device ID: %s\n", DEVICE_ID);

    ServoHead::init();  // parks the head before anything else can move it
    OledEyes::init();
    PirInput::init();
    DeskbotController::init();
    WiFiManager::init();
    MqttClient::init(onCommand);
    Serial.println("[ThingWire] Boot complete.");
}

void loop() {
    WiFiManager::tick();
    MqttClient::tick();
    PirInput::tick();
    DeskbotController::tick();
    ServoHead::tick();
    OledEyes::tick();

    if (!MqttClient::isConnected()) {
        return;
    }
    if (!_tdPublished) {
        char* td = DeskbotTd::generate();
        if (td) {
            MqttClient::publishRetained("td", td);
            Serial.println("[ThingWire] Published desk bot TD");
            free(td);
            _tdPublished = true;
        }
    }
    if (PirInput::takePersonSeen()) {
        char evt[96];
        snprintf(evt, sizeof(evt), "{\"event\":\"person_seen\",\"data\":{\"confidence\":%.2f}}",
                 (double)DESKBOT_PERSON_CONFIDENCE);
        MqttClient::publishEvent(evt);
    }
    unsigned long now = millis();
    if (now - _lastTelemetryMs >= DESKBOT_TELEMETRY_MS) {
        _lastTelemetryMs = now;
        publishTelemetry();
    }
}
