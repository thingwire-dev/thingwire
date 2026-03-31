#ifndef WOT_TD_GENERATOR_H
#define WOT_TD_GENERATOR_H

namespace WotTdGenerator {
    /// Generate WoT Thing Description JSON string
    /// Caller must free the returned string with free()
    char* generate();
}

#endif // WOT_TD_GENERATOR_H
