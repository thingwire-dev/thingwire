package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func testService() *service {
	return &service{cfg: config{gatewayToken: "gateway-token-0123456789abc", operatorToken: "operator-token-0123456789ab"}}
}

func TestTokenSplit(t *testing.T) {
	s := testService()
	ok := func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) }
	routes := map[string]http.HandlerFunc{"operator": s.operator(ok), "gateway": s.gateway(ok)}
	cases := []struct {
		route, header string
		want          int
	}{
		{"operator", "Bearer operator-token-0123456789ab", 204},
		{"operator", "Bearer gateway-token-0123456789abc", 401},
		{"operator", "", 401},
		{"operator", "Bearer ", 401},
		{"operator", "Basic operator-token-0123456789ab", 401},
		{"gateway", "Bearer gateway-token-0123456789abc", 204},
		{"gateway", "Bearer operator-token-0123456789ab", 401},
		{"gateway", "", 401},
	}
	for _, c := range cases {
		req := httptest.NewRequest("POST", "/x", strings.NewReader("{}"))
		if c.header != "" {
			req.Header.Set("Authorization", c.header)
		}
		w := httptest.NewRecorder()
		routes[c.route](w, req)
		if w.Code != c.want {
			t.Errorf("%s with %q: got %d want %d", c.route, c.header, w.Code, c.want)
		}
	}
	req := httptest.NewRequest("GET", "/approvals", nil)
	req.Header.Set("Authorization", "Bearer gateway-token-0123456789abc")
	if _, err := s.authenticate(req); err == nil {
		t.Error("gateway token must not authenticate the approvals inbox")
	}
}

func TestConfigRequiresDistinctTokens(t *testing.T) {
	base := map[string]string{"THEAUTH_ENCRYPTION_KEY": strings.Repeat("ab", 32)}
	get := func(extra map[string]string) func(string) string {
		return func(k string) string {
			if v, ok := extra[k]; ok {
				return v
			}
			return base[k]
		}
	}
	same := "same-token-0123456789abcdefgh"
	if _, err := loadConfig(get(map[string]string{"THEAUTH_GATEWAY_TOKEN": same, "THEAUTH_OPERATOR_TOKEN": same})); err == nil {
		t.Error("identical tokens must be refused")
	}
	if _, err := loadConfig(get(map[string]string{"THEAUTH_GATEWAY_TOKEN": same})); err == nil {
		t.Error("missing operator token must be refused")
	}
}
