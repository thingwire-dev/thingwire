# Desk bot behaviour loop (simulation)

A small deterministic state machine that gives the virtual desk bot a personality. It runs
in simulation only: a scripted PIR, a simulated clock, the repo's virtual desk bot. No
hardware, no network, no model call unless you opt in (see below).

```
python examples/desk-bot-behaviour/run_sim.py [--seed 7] [--seconds 300] [--mcap run.mcap]
```

It finishes instantly and prints a timeline of states and actions. Same seed, same timeline.
`--mcap` records the gateway calls and decisions with the existing recorder.

## States

| State | What it does |
| --- | --- |
| IDLE | Occasional blink (logged, the TD has no blink action) and a look-around, sometimes with the curious face. |
| NOTICE | Steady PIR seen: turn the head to the front, show the alert face. |
| REACT | Short expressive response: an expression from the chooser, maybe a nod, maybe a short line shown on the face (logged only, the TD has no display action). |
| COOLDOWN | Return to a neutral face and a centred head. PIR is ignored until it has gone low once. |
| SLEEP | After long inactivity: sleepy face, head down. Steady PIR wakes it into NOTICE. |
| HALTED | Terminal. Entered on a latched e-stop, a deadman trip or any exception. Only a restart leaves it. |

A PIR that flaps (high for less than `trigger_after_s`) is ignored, so it never triggers NOTICE.

## Safety

Every command goes through the real gateway `ActionPipeline` (identity scopes, taint,
`SafetyLayer`, audit) using the injection benchmark's gateway arm. The bot has only the
`look_at`, `set_expression` and `nod` scopes. A denial is logged, then the loop pauses
(`backoff`, doubling) and carries on; it never retries in a tight loop. The loop sends a
deadman heartbeat every step, checks the latched e-stop every step and halts for good when
it is set. Resetting an e-stop is a human action outside this loop.

## personality.yaml

All timings, probabilities and expressions live there and nowhere else. It is validated at
start and the loop refuses to run on any missing or unknown key, wrong type, out-of-range
number, or an expression that is not in the TD enum. Edit the numbers freely, for example:

- `idle.blink_every_s`, `idle.look_every_s`: `[min, max]` seconds, drawn at random.
- `idle.curiosity`: chance a look-around also shows the curious face.
- `react.utterance_probability`, `react.nod_probability`: how chatty and nodding it is.
- `sleep.after_idle_s`: how long with no motion before it sleeps.
- `menu.utterances`: the only lines it can ever say (letters, digits, light punctuation).

Keep `min_action_gap_s` and the look range modest: the safety layer rate limits calls and
caps head speed, and it will deny a request that asks for more.

## Optional LLM chooser

The chooser only decides which expression and line the bot uses in REACT. The default is
rule based and seeded. To try a model instead, set all of these, then pass `--llm`:

```
THINGWIRE_BEHAVIOUR_LLM=1
THINGWIRE_LLM_URL=https://...        # chat-completions style endpoint, https only
THINGWIRE_LLM_KEY=...
THINGWIRE_LLM_MODEL=...
```

Without all four nothing is called. The prompt holds only the state name and the menu, never
scene or sensor text. The reply must be JSON naming one menu expression and either null or
one menu line; anything else (out of menu, too long, not JSON, an error) is discarded and the
rule-based choice is used. Tests never call it.

## Limits

- Simulation only. The PIR is a scripted list of intervals, not a sensor.
- The TD has no blink, speech or display action, so blinks and lines appear in the timeline
  only. Showing them on the face needs a TD action first.
- Safety is the benchmark's gateway arm with a scripted approver that never approves;
  nothing here is a deployment configuration.
