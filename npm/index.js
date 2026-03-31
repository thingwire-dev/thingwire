#!/usr/bin/env node

/**
 * ThingWire MCP Server — npm wrapper for the Python package.
 *
 * This spawns `thingwire serve` which runs the Python MCP server
 * over stdio. Install the Python package first: pip install thingwire
 */

const { spawn } = require("child_process");

const args = ["serve"];

// Forward environment variables
const env = { ...process.env };

const child = spawn("thingwire", args, {
  stdio: ["inherit", "inherit", "inherit"],
  env,
});

child.on("error", (err) => {
  if (err.code === "ENOENT") {
    process.stderr.write(
      "Error: 'thingwire' not found. Install the Python package first:\n" +
      "  pip install thingwire\n\n" +
      "Then run: thingwire-mcp\n"
    );
    process.exit(1);
  }
  process.stderr.write(`Error: ${err.message}\n`);
  process.exit(1);
});

child.on("exit", (code) => {
  process.exit(code ?? 0);
});
