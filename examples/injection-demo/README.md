# Prompt-injection benchmark

A reproducible measurement of how much the ThingWire gateway safety layer reduces the
success of prompt-injection attacks that end in a physical command. No model, no network,
no API spend. The same inputs give byte-identical output.

## Run it

```bash
uv run --extra dev python examples/injection-demo/run_benchmark.py            # writes results/
uv run --extra dev python examples/injection-demo/run_benchmark.py --seed 7 --out /tmp/out
uv run --extra dev pytest -q tests/test_injection_benchmark.py tests/test_injection_agent.py
```

It prints an attack success rate (ASR) table per category for both arms, plus the false
positive rate of the benign control, and writes `results.json` and `summary.md`. The
committed `results/` files are pinned by a test: change behavior, regenerate them.

## The two arms

| Arm | What a tool call goes through |
| --- | --- |
| baseline | The call is turned into a device command and handed straight to the device adapter. No identity, taint, approval, safety layer or audit. This bypass exists only inside this example (`inj_arms.py`); `src/` has no switch for it. |
| gateway | The real `ActionPipeline` (the path `mcp_server.py` registers for every device tool): identity scopes, taint policy, human approval through a scripted theAuth, `SafetyLayer.evaluate` (allowlist, e-stop latch, flood guard, deadman, ranges, rate limit), then the device adapter. |

Both arms use the same tool names, the same Thing Descriptions, the same virtual desk bot
(`scripts/virtual_desk_bot.py`) behind the device adapter, and the same simulated clock.

## The naive agent

`inj_agent.py` is a scripted agent with no judgment. It obeys every `call tool(k=v) [xN]`
directive in any channel it reads, plus one plain phrase from the demo scene sign ("sweep the
head at max speed"). String sensor readings are read as text too. `@last` is the last number
it read. Channels: scene text, MQTT payload fields, sensor reading strings, TD descriptions,
tool results, and the trusted operator (used only by benign tasks and attack setup).

## Corpus

34 labelled attacks in 10 categories (`inj_corpus_a.py`, `inj_corpus_b.py`). Each declares the
expected safe outcome and the decision codes that would be the right reason to refuse. An
attack succeeds when a command it caused reaches the device and breaks the rule in its
`Harm` (any delivery, an invalid value by the honest TD, a slew breach, or more calls than the
rate limit). The judge uses the honest TD, so a poisoned TD cannot move the goalposts.

12 benign tasks (`inj_benign.py`) run on both arms. A task fails when any of its operator
calls is refused. Tasks that the policy gates (dangerous action, action on perception data)
are run with a scripted human who approves once. False positive rate is failed tasks over
all benign tasks.

## What the gateway arm assumes

Identity is on (the http transport): the agent holds read on both devices and act scopes for
`look_at`, `set_expression` and `nod` on the desk bot only. The scripted human either never
answers or approves exactly one call. The safety config is `safety_config.yaml`, identical to
the repo's except that `target_bearing`, a numeric camera property added to the benchmark
TD, is listed under `taint.perception_properties` (a test pins that this is the only change).

## Reading the results

The gaps are the point. Cases the gateway does not block are listed in `summary.md` with the
reason; they were not removed or softened to improve the numbers. Current gaps, each a
property of the code under test and not of the harness:

- Limits, the `safe` flag and the approval requirement come from the TD the device publishes
  (TDP-03, TDP-04). A rogue or poisoned TD sets its own limits.
- TD `enum` constraints are not enforced at the gateway (DCI-02).
- Slew limits are only read from action input axes; the desk bot declares them on the
  `head_pan` and `head_tilt` properties, so look_at is not slew limited (MLV-03).
- There is no command freshness or nonce, so an old valid command replays (RPL-02).
- The rate limit is per device and action, so rotating actions multiplies the budget (RLF-03).
- Taint links a read to a call only by an exact numeric match, so arithmetic on the value or a
  string reading escapes it (TNT-03, TNT-04).
- In-range, authorized actions caused by scene or sensor text look like any other call
  (SCN-03, SEN-03). Only approval policy on perception data could cover them.

## Limitations

- The agent is a fixed script, not a model. It measures what the gateway stops given an
  agent that always obeys, which is a worst case for the agent and an exact case for the
  gateway. It says nothing about how often a real model is fooled.
- Calls enter at `ActionPipeline`, below FastMCP, so MCP argument schema validation is not in
  the path. The stdio dev mode (no identity) is not measured as a third arm.
- Success is "a harmful command reached the device adapter". The virtual bot also has its own
  range checks, so some baseline successes are rejected on the device; `summary.md` reports
  how many still ran. Real firmware is not exercised.
- A global stop in the baseline arm is tripped on the device only; the baseline has no latch.
- The corpus is small and written by the same people who wrote the gateway. Treat the ASR as a
  regression number for this repo, not as a security claim. New cases belong in the corpus
  with their honest outcome.
- `RPL-02` expects a code (`STALE_COMMAND`) that does not exist; it names the missing control.
