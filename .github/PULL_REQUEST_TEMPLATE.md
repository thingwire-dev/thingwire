<!-- Target: your PR goes into release/<name>, never main. Only the release PR targets main. -->

## What and why

## Contract changes
<!-- Edits to docs/contracts.md (TD schema, MQTT topics, scopes, audit columns, Decision codes)? Link the contract PR that landed first, or write "none". -->

## Files touched vs ownership
<!-- Branch: ____ . Any shared file (see .agents/ownership.yaml)? Say which and link the request approved by the owner, or write "none". -->

## Test evidence
<!-- Paste the output of `make verify` (or the command and result). -->

## Safety impact
<!-- Does this touch the safety layer, identity, actuators or firmware limits? Does anything fail open? Write "none" only if true. -->

## Rollback
<!-- How to undo: revert this squash commit, or any extra steps (config, firmware reflash). -->

## Checklist
- [ ] `make verify` is green
- [ ] No em or en dashes
- [ ] Commits are `<type>: <description>` with no co-author or AI-attribution lines
- [ ] Branch is not main or a release branch, PR base is the release branch
