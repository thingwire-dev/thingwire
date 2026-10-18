#ifndef DESKBOT_CONTROLLER_H
#define DESKBOT_CONTROLLER_H

namespace DeskbotController {
    /// Reset command state (call after ServoHead and OledEyes init)
    void init();

    /// Call in loop() - runs nod and sweep sequences and the deadman park (non-blocking)
    void tick();

    /// Handle an MQTT command JSON, returns ack JSON. Caller must free() it
    char* handleCommand(const char* payload);

    /// True while the head is parked because the deadman timer fired
    bool isDeadmanParked();
}

#endif // DESKBOT_CONTROLLER_H
