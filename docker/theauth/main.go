// Command theauth is the ThingWire identity service: an OAuth 2.1 authorization server with agent
// identities, token exchange and tool approvals, on one SQLite file (theauth-go).
package main

import (
	"context"
	"errors"
	"log"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"time"
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()

	cfg, err := loadConfig(os.Getenv)
	if err != nil {
		log.Fatalf("config: %v", err)
	}
	s, err := newService(ctx, cfg)
	if err != nil {
		log.Fatalf("start: %v", err)
	}
	defer s.close()

	srv := &http.Server{Addr: cfg.addr, Handler: s.handler, ReadHeaderTimeout: 10 * time.Second}
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = srv.Shutdown(shutdown)
	}()
	slog.Info("theauth listening", "addr", cfg.addr, "issuer", cfg.issuer, "resource", cfg.resource)
	if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatal(err)
	}
}

// config holds every setting. There are no insecure defaults for secrets: the encryption key and
// the admin token must be supplied.
type config struct {
	addr, issuer, resource, dbPath string
	scopes                         []string
	encryptionKey                  []byte
	adminToken                     string
	approvalTTL                    time.Duration
	tokenTTL                       time.Duration
}

const minAdminTokenLen = 24

func loadConfig(get func(string) string) (config, error) {
	env := func(k, def string) string {
		if v := get(k); v != "" {
			return v
		}
		return def
	}
	c := config{
		addr:       env("THEAUTH_ADDR", ":9200"),
		issuer:     strings.TrimRight(env("THEAUTH_ISSUER", "http://localhost:9200"), "/"),
		resource:   env("THEAUTH_RESOURCE", "http://localhost:8000/mcp"),
		dbPath:     env("THEAUTH_DB_PATH", "/data/theauth.db"),
		adminToken: get("THEAUTH_ADMIN_TOKEN"),
		scopes: splitList(env("THEAUTH_SCOPES", strings.Join([]string{
			"device:thingwire-demo-001:read",
			"device:thingwire-demo-001:act:setRelay",
			"device:thingwire-demo-001:act:dangerous",
			"device:desk-bot:read",
			"device:desk-bot:act:look_at",
			"device:desk-bot:act:sweep",
			"device:desk-bot:act:dangerous",
			"audit:read",
		}, ","))),
	}
	var err error
	if c.approvalTTL, err = time.ParseDuration(env("THEAUTH_APPROVAL_TTL", "5m")); err != nil {
		return c, errors.New("THEAUTH_APPROVAL_TTL: " + err.Error())
	}
	if c.tokenTTL, err = time.ParseDuration(env("THEAUTH_TOKEN_TTL", "15m")); err != nil {
		return c, errors.New("THEAUTH_TOKEN_TTL: " + err.Error())
	}
	if c.encryptionKey, err = decodeKey(get("THEAUTH_ENCRYPTION_KEY")); err != nil {
		return c, err
	}
	if len(c.adminToken) < minAdminTokenLen {
		return c, errors.New("THEAUTH_ADMIN_TOKEN must be set to at least 24 characters")
	}
	return c, nil
}

func splitList(s string) []string {
	var out []string
	for _, p := range strings.Split(s, ",") {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}
