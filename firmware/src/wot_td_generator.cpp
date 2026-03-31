#include "wot_td_generator.h"
#include "config.h"
#include "wifi_manager.h"
#include <Arduino.h>
#include <ArduinoJson.h>

namespace WotTdGenerator {

char* generate() {
    JsonDocument doc;

    doc["@context"] = "https://www.w3.org/2019/wot/td/v1.1";
    doc["@type"] = "Thing";

    char idBuf[64];
    snprintf(idBuf, sizeof(idBuf), "urn:thingwire:device:%s", DEVICE_ID);
    doc["id"] = idBuf;
    doc["title"] = "ThingWire Demo Device";
    doc["description"] = "ESP32-S3 with temperature, humidity, motion sensors and relay actuator";

    // Security
    doc["securityDefinitions"]["nosec_sc"]["scheme"] = "nosec";
    JsonArray security = doc["security"].to<JsonArray>();
    security.add("nosec_sc");

    // Build MQTT base URL
    char mqttBase[128];
    snprintf(mqttBase, sizeof(mqttBase), "mqtt://%s/thingwire/%s",
             WiFiManager::getMqttBroker(), DEVICE_ID);

    // Properties
    JsonObject props = doc["properties"].to<JsonObject>();

    // Temperature
    JsonObject temp = props["temperature"].to<JsonObject>();
    temp["type"] = "number";
    temp["unit"] = "celsius";
    temp["readOnly"] = true;
    temp["description"] = "Current temperature reading from DHT22 sensor";
    JsonArray tempForms = temp["forms"].to<JsonArray>();
    JsonObject tempForm = tempForms.add<JsonObject>();
    char telemetryHref[192];
    snprintf(telemetryHref, sizeof(telemetryHref), "%s/telemetry", mqttBase);
    tempForm["href"] = telemetryHref;
    tempForm["op"] = "observeproperty";

    // Humidity
    JsonObject hum = props["humidity"].to<JsonObject>();
    hum["type"] = "number";
    hum["unit"] = "percent";
    hum["readOnly"] = true;
    hum["description"] = "Current humidity reading from DHT22 sensor";
    JsonArray humForms = hum["forms"].to<JsonArray>();
    JsonObject humForm = humForms.add<JsonObject>();
    humForm["href"] = telemetryHref;
    humForm["op"] = "observeproperty";

    // Motion
    JsonObject motion = props["motion"].to<JsonObject>();
    motion["type"] = "boolean";
    motion["readOnly"] = true;
    motion["description"] = "PIR motion sensor - true when motion detected";
    JsonArray motionForms = motion["forms"].to<JsonArray>();
    JsonObject motionForm = motionForms.add<JsonObject>();
    motionForm["href"] = telemetryHref;
    motionForm["op"] = "observeproperty";

    // Actions
    JsonObject actions = doc["actions"].to<JsonObject>();
    JsonObject relay = actions["setRelay"].to<JsonObject>();
    relay["title"] = "Control relay";
    relay["description"] = "Turn relay on or off. Controls a physical device connected to the relay.";
    relay["safe"] = false;
    relay["idempotent"] = true;

    JsonObject input = relay["input"].to<JsonObject>();
    input["type"] = "object";
    JsonObject inputProps = input["properties"].to<JsonObject>();
    JsonObject stateParam = inputProps["state"].to<JsonObject>();
    stateParam["type"] = "boolean";
    stateParam["description"] = "true = on, false = off";
    JsonArray required = input["required"].to<JsonArray>();
    required.add("state");

    JsonArray relayForms = relay["forms"].to<JsonArray>();
    JsonObject relayForm = relayForms.add<JsonObject>();
    char commandHref[192];
    snprintf(commandHref, sizeof(commandHref), "%s/command", mqttBase);
    relayForm["href"] = commandHref;
    relayForm["op"] = "invokeaction";

    // Serialize
    size_t len = measureJson(doc) + 1;
    char* result = (char*)malloc(len);
    if (result) {
        serializeJson(doc, result, len);
    }

    Serial.printf("[WoT] Generated TD (%d bytes)\n", (int)len);
    return result;
}

} // namespace WotTdGenerator
