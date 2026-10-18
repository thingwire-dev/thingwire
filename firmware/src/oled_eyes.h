#ifndef OLED_EYES_H
#define OLED_EYES_H

namespace OledEyes {
    enum Expression {
        NEUTRAL = 0,
        HAPPY,
        CURIOUS,
        SLEEPY,
        SURPRISED,
        ALERT,
        EXPRESSION_COUNT
    };

    /// Start the SSD1306 on I2C and show the neutral frame
    void init();

    /// Call in loop() - advances the blink animation and redraws when needed (non-blocking)
    void tick();

    /// Switch the frame set
    void setExpression(Expression e);

    /// Current expression
    Expression getExpression();

    /// Parse an expression name (as in the TD enum); returns false if unknown
    bool parseExpression(const char* name, Expression* out);

    /// Name of an expression (matches the TD enum)
    const char* expressionName(Expression e);
}

#endif // OLED_EYES_H
