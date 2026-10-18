package main

import (
	"context"
	"database/sql"
	"encoding/hex"
	"errors"
	"fmt"
	"net/http"

	sqlitestore "github.com/glincker/theauth-go/storage/sqlite"
	"github.com/glincker/theauth-go/v2"
	"github.com/glincker/theauth-go/v2/approvals"
	"github.com/glincker/theauth-go/v2/policy/toolguard"
	_ "modernc.org/sqlite"
)

const (
	ownerEmail = "gateway-owner@thingwire.local"
	operatorID = "operator"
)

type service struct {
	cfg       config
	auth      *theauth.TheAuth
	db        *sql.DB
	approvals *toolguard.StorageApprovals
	store     theauth.ToolApprovalAdminStorage
	owner     theauth.User
	handler   http.Handler
}

func (s *service) close() {
	s.auth.Close()
	_ = s.db.Close()
}

func decodeKey(h string) ([]byte, error) {
	key, err := hex.DecodeString(h)
	if err != nil || len(key) != 32 {
		return nil, errors.New("THEAUTH_ENCRYPTION_KEY must be 64 hex characters (32 bytes), e.g. openssl rand -hex 32")
	}
	return key, nil
}

func newService(ctx context.Context, cfg config) (*service, error) {
	dsn := "file:" + cfg.dbPath + "?_pragma=foreign_keys(1)&_pragma=busy_timeout(5000)&_pragma=journal_mode(WAL)&_txlock=immediate"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("open database: %w", err)
	}
	fail := func(format string, err error) (*service, error) {
		_ = db.Close()
		return nil, fmt.Errorf(format, err)
	}
	if err := db.PingContext(ctx); err != nil {
		return fail("ping database: %w", err)
	}
	if err := sqlitestore.Migrate(ctx, db); err != nil {
		return fail("migrate: %w", err)
	}
	store, err := sqlitestore.New(db)
	if err != nil {
		return fail("storage: %w", err)
	}
	a, err := theauth.New(theauth.Config{
		Storage:                       store,
		BaseURL:                       cfg.issuer,
		EncryptionKey:                 cfg.encryptionKey,
		SuppressSecureCookieWarning:   true,
		SuppressTrustedProxiesWarning: true,
		AuthorizationServer: &theauth.AuthorizationServerConfig{
			Issuer:                cfg.issuer,
			Resources:             []theauth.ProtectedResource{{Identifier: cfg.resource, DisplayName: "ThingWire gateway", Scopes: cfg.scopes}},
			AccessTokenTTL:        cfg.tokenTTL,
			AccessTokenRevocation: true,
		},
		AgentIdentity: &theauth.AgentConfig{DefaultDelegatedTokenTTL: cfg.tokenTTL},
	})
	if err != nil {
		return fail("theauth: %w", err)
	}
	s := &service{cfg: cfg, auth: a, db: db, store: store}
	s.approvals = toolguard.NewStorageApprovals(store,
		toolguard.WithApprovalTTL(cfg.approvalTTL), toolguard.WithApprovalAudit(a.EmitAudit))
	if s.owner, err = a.CreateUser(ctx, theauth.CreateUserInput{Email: ownerEmail, Name: "ThingWire gateway owner"}); err != nil {
		s.close()
		return nil, fmt.Errorf("owner user: %w", err)
	}
	inbox, err := approvals.New(approvals.Config{
		Store: store, Audit: store,
		Authenticate: s.authenticate,
		IsAdmin:      func(_ context.Context, id string) bool { return id == operatorID },
	})
	if err != nil {
		s.close()
		return nil, fmt.Errorf("approvals inbox: %w", err)
	}
	s.handler = s.routes(a.Handler(), inbox)
	return s, nil
}
