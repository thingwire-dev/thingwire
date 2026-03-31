#include "actuator_controller.h"
#include "config.h"
#include <Arduino.h>
#include <ArduinoJson.h>

namespace ActuatorController {

static bool _relayState = false;

void init() {
    pinMode(RELAY_PIN, OUTPUT);
    digitalWrite(RELAY_PIN, LOW);
    _relayState = false;
    Serial.println("[Actuator] Relay initialized (OFF)");
}

char* handleCommand(const char* payload) {
    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, payload);

    JsonDocument ack;

    if (err) {
        Serial.printf("[Actuator] Invalid command JSON: %s\n", err.c_str());
        ack["status"] = "error";
        ack["error"] = "Invalid JSON";
        goto serialize;
    }

    {
        const char* actionId = doc["action_id"] | "unknown";
        const char* target = doc["target"] | "unknown";
        const char* command = doc["command"] | "unknown";

        ack["action_id"] = actionId;
        ack["target"] = target;
        ack["command"] = command;

        if (strcmp(target, "relay1") == 0 && strcmp(command, "set") == 0) {
            bool value = doc["value"] | false;
            _relayState = value;
            digitalWrite(RELAY_PIN, _relayState ? HIGH : LOW);

            ack["status"] = "ok";
            ack["value"] = _relayState;
            Serial.printf("[Actuator] Relay set to: %s\n", _relayState ? "ON" : "OFF");
        } else {
            ack["status"] = "error";
            ack["error"] = "Unknown target/command";
            Serial.printf("[Actuator] Unknown: target=%s command=%s\n", target, command);
        }
    }

serialize:
    char* result = (char*)malloc(512);
    if (result) {
        serializeJson(ack, result, 512);
    }
    return result;
}

bool getRelayState() {
    return _relayState;
}

} // namespace ActuatorController
