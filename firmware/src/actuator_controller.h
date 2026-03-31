#ifndef ACTUATOR_CONTROLLER_H
#define ACTUATOR_CONTROLLER_H

namespace ActuatorController {
    /// Initialize relay pin
    void init();

    /// Process incoming MQTT command JSON, returns ack JSON string
    /// Caller must free the returned string with free()
    char* handleCommand(const char* payload);

    /// Get current relay state
    bool getRelayState();
}

#endif // ACTUATOR_CONTROLLER_H
