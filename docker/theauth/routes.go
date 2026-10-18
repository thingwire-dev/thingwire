package main

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"time"

	"github.com/glincker/theauth-go/v2"
	"github.com/glincker/theauth-go/v2/policy"
	"github.com/glincker/theauth-go/v2/policy/toolguard"
	"github.com/oklog/ulid/v2"
)

const maxBody = 64 << 10

func bearerIs(r *http.Request, want string) bool {
	got, ok := strings.CutPrefix(r.Header.Get("Authorization"), "Bearer ")
	return ok && got != "" && subtle.ConstantTimeCompare([]byte(got), []byte(want)) == 1
}

// authenticate accepts only the operator token (agent creation and the approvals inbox). The
// gateway token never satisfies it, so a compromised gateway cannot approve its own requests.
func (s *service) authenticate(r *http.Request) (string, error) {
	if !bearerIs(r, s.cfg.operatorToken) {
		return "", errors.New("unauthenticated")
	}
	return operatorID, nil
}

// operator guards endpoints only a human operator may call.
func (s *service) operator(next http.HandlerFunc) http.HandlerFunc {
	return s.guard(func(r *http.Request) bool { return bearerIs(r, s.cfg.operatorToken) }, next)
}

// gateway guards the endpoints the gateway uses: open and spend approvals, nothing else.
func (s *service) gateway(next http.HandlerFunc) http.HandlerFunc {
	return s.guard(func(r *http.Request) bool { return bearerIs(r, s.cfg.gatewayToken) }, next)
}

func (s *service) guard(ok func(*http.Request) bool, next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if !ok(r) {
			writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthenticated"})
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, maxBody)
		next(w, r)
	}
}

func (s *service) routes(oauth, inbox http.Handler) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /thingwire/agents", s.operator(s.createAgent))
	mux.HandleFunc("POST /thingwire/approvals", s.gateway(s.openApproval))
	mux.HandleFunc("POST /thingwire/approvals/{id}/spend", s.gateway(s.spendApproval))
	mux.Handle("/approvals", inbox)
	mux.Handle("/approvals/", inbox)
	mux.Handle("/", oauth)
	return mux
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func decode(r *http.Request, v any) error {
	dec := json.NewDecoder(r.Body)
	dec.DisallowUnknownFields()
	return dec.Decode(v)
}

// createAgent registers an agent limited to the requested scopes. Scopes outside the resource
// catalog are refused at mint time by the authorization server.
func (s *service) createAgent(w http.ResponseWriter, r *http.Request) {
	var in struct {
		Name   string   `json:"name"`
		Scopes []string `json:"scopes"`
	}
	if err := decode(r, &in); err != nil || in.Name == "" || len(in.Scopes) == 0 {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "name and scopes are required"})
		return
	}
	agent, secret, err := s.auth.CreateAgent(r.Context(), theauth.CreateAgentInput{
		Owner: theauth.AgentOwner{UserID: &s.owner.ID}, Name: in.Name, Scope: in.Scopes,
	})
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "create agent failed"})
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{
		"agent_id": agent.ID.String(), "client_id": secret.ClientID, "client_secret": secret.Secret, "scopes": agent.Scope,
	})
}

type callBody struct {
	AgentID   string         `json:"agent_id"`
	DeviceID  string         `json:"device_id"`
	Action    string         `json:"action"`
	Arguments map[string]any `json:"arguments"`
	Reason    string         `json:"reason"`
}

func (c callBody) call() (toolguard.Call, bool) {
	if _, err := ulid.Parse(c.AgentID); err != nil || c.DeviceID == "" || c.Action == "" {
		return toolguard.Call{}, false
	}
	return toolguard.Call{AgentID: c.AgentID, Tool: c.DeviceID + ":" + c.Action, Args: c.Arguments}, true
}

// openApproval opens a pending approval bound to the agent, device, action and exact arguments.
func (s *service) openApproval(w http.ResponseWriter, r *http.Request) {
	var in callBody
	call, ok := toolguard.Call{}, false
	if err := decode(r, &in); err == nil {
		call, ok = in.call()
	}
	if !ok {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "agent_id, device_id and action are required"})
		return
	}
	if n, err := s.pendingFor(r.Context(), in.AgentID); err != nil || n >= s.cfg.maxPending {
		code := http.StatusTooManyRequests
		if err != nil {
			code = http.StatusInternalServerError
		}
		writeJSON(w, code, map[string]string{"error": "approval limit", "code": "approval_limit"})
		return
	}
	reason := in.Reason
	if reason == "" {
		reason = "dangerous action " + in.Action + " on " + in.DeviceID
	}
	id, err := s.approvals.Open(r.Context(), call, policy.Escalation{Requirement: policy.RequireApproval, Tier: "dangerous", Reason: reason})
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "open approval failed"})
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{"approval_id": id, "ttl_seconds": int(s.cfg.approvalTTL.Seconds())})
}

// spendApproval reports the approval state for these exact arguments and, when approved, spends
// it (single use). The code is approval_consumed only when the caller may proceed.
func (s *service) spendApproval(w http.ResponseWriter, r *http.Request) {
	var in callBody
	call, ok := toolguard.Call{}, false
	if err := decode(r, &in); err == nil {
		call, ok = in.call()
	}
	if !ok {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "agent_id, device_id and action are required"})
		return
	}
	code, err := s.approvals.Spend(r.Context(), r.PathValue("id"), call)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "spend failed", "code": "approval_error"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"code": string(code), "proceed": code == policy.CodeApprovalConsumed})
}

// pendingFor counts undecided, unexpired approvals the agent already has open.
func (s *service) pendingFor(ctx context.Context, agentID string) (int, error) {
	list, err := s.store.ListToolApprovals(ctx, 100)
	if err != nil {
		return 0, err
	}
	now, n := time.Now(), 0
	for _, a := range list {
		undecided := a.ApprovedAt.IsZero() && a.DeniedAt.IsZero() && a.ConsumedAt.IsZero() && a.ExpiredAt.IsZero()
		if a.AgentID == agentID && undecided && (a.ExpiresAt.IsZero() || a.ExpiresAt.After(now)) {
			n++
		}
	}
	return n, nil
}
