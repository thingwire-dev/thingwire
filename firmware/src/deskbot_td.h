#ifndef DESKBOT_TD_H
#define DESKBOT_TD_H

namespace DeskbotTd {
    /// Build the desk bot Thing Description (template from device-deskbot.yaml with
    /// device id and broker filled in). Caller must free() the result
    char* generate();
}

#endif // DESKBOT_TD_H
