#ifndef PIR_INPUT_H
#define PIR_INPUT_H

namespace PirInput {
    /// Configure the PIR pin
    void init();

    /// Call in loop() - samples the pin and holds person_detected for a grace period
    void tick();

    /// Value of the person_detected property
    bool personDetected();

    /// True once per rising edge of person_detected (rate limited), then clears
    bool takePersonSeen();
}

#endif // PIR_INPUT_H
