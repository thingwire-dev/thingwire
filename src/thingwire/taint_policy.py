"""Taint-aware policy: actions caused by untrusted-origin context need approval.

Config comes from the `taint:` section of safety_config.yaml, for example:

    taint:
      enabled: true
      extra_scope: "taint:override"
      untrusted_origins: [untrusted_perception]
      default_mode: require_approval        # require_approval | allow | deny
      read_actions: [read_scene, get_ranges]
      actions:
        sweep: {mode: require_approval}
        status: {mode: allow}
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any

from thingwire.provenance import Origin, Provenance
from thingwire.safety_motion import Decision

CODE_REQUIRES_APPROVAL = "TAINTED_CONTEXT_REQUIRES_APPROVAL"
CODE_DENIED = "TAINTED_CONTEXT_DENIED"
CODE_OK = "TAINT_OK"
CODE_READ = "TAINT_READ_ALLOWED"
CODE_OVERRIDDEN = "TAINT_OVERRIDDEN"
CODE_MALFORMED = "TAINT_MALFORMED_INPUT"

MODES = frozenset({"require_approval", "allow", "deny"})


@dataclass(frozen=True)
class TaintPolicy:
    enabled: bool = True
    extra_scope: str = "taint:override"
    untrusted_origins: frozenset[Origin] = frozenset({Origin.UNTRUSTED_PERCEPTION})
    default_mode: str = "require_approval"
    read_actions: frozenset[str] = frozenset()
    action_modes: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> "TaintPolicy":
        """Build from the full safety config or the `taint:` section. Raises ValueError."""
        if config is None:
            return cls()
        section = config.get("taint", config) if "taint" in config else config
        if not isinstance(section, Mapping):
            raise ValueError("taint config must be a mapping")
        mode = section.get("default_mode", "require_approval")
        if mode not in MODES:
            raise ValueError(f"invalid taint default_mode: {mode!r}")
        origins = section.get("untrusted_origins", [Origin.UNTRUSTED_PERCEPTION.value])
        reads = section.get("read_actions", [])
        if not isinstance(origins, list) or not isinstance(reads, list):
            raise ValueError("untrusted_origins and read_actions must be lists")
        modes: dict[str, str] = {}
        actions = section.get("actions")
        if actions is None:
            actions = {}
        if not isinstance(actions, Mapping):
            raise ValueError("taint actions must be a mapping")
        for name, spec in actions.items():
            m = spec.get("mode") if isinstance(spec, Mapping) else None
            if m not in MODES:
                raise ValueError(f"invalid taint mode for action {name!r}: {m!r}")
            modes[str(name)] = m
        scope = section.get("extra_scope", "taint:override")
        if not isinstance(scope, str) or not scope:
            raise ValueError("extra_scope must be a non-empty string")
        return cls(
            enabled=bool(section.get("enabled", True)),
            extra_scope=scope,
            untrusted_origins=frozenset(Origin(o) for o in origins),
            default_mode=mode,
            read_actions=frozenset(str(r) for r in reads),
            action_modes=modes,
        )

    def evaluate(
        self,
        action: str,
        context: Collection[Provenance],
        *,
        is_read: bool = False,
        granted_scopes: Collection[str] = (),
        approved: bool = False,
    ) -> Decision:
        """Decide an action given provenance seen since the last operator instruction."""
        if (
            not isinstance(action, str)
            or not action
            or not all(isinstance(p, Provenance) for p in context)
        ):
            return Decision(False, CODE_MALFORMED, "malformed action or taint context")
        if is_read or action in self.read_actions:
            return Decision(True, CODE_READ, "pure read allowed")
        tainted = sorted(p.taint_id for p in context if p.origin in self.untrusted_origins)
        mode = self.action_modes.get(action, self.default_mode)
        if not self.enabled or not tainted or mode == "allow":
            return Decision(True, CODE_OK, "no untrusted context", {"taint_ids": tainted})
        details = {"taint_ids": tainted, "required_scope": self.extra_scope, "action": action}
        if mode == "deny":
            return Decision(
                False, CODE_DENIED, "untrusted context may not cause this action", details
            )
        if approved or self.extra_scope in granted_scopes:
            return Decision(True, CODE_OVERRIDDEN, "tainted action approved", details)
        return Decision(
            False,
            CODE_REQUIRES_APPROVAL,
            f"action caused by untrusted context needs approval or scope {self.extra_scope}",
            details,
        )
