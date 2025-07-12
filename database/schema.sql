-- Enable foreign key support
PRAGMA foreign_keys = ON;

-- Users table (cache user information)
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT UNIQUE,
    first_name TEXT,
    last_name TEXT,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Recipients table (both personal and shared)
CREATE TABLE IF NOT EXISTS recipients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    platform_type TEXT NOT NULL,
    credentials TEXT NOT NULL DEFAULT '',
    platform_config TEXT,
    is_personal BOOLEAN NOT NULL DEFAULT TRUE,
    is_default BOOLEAN DEFAULT FALSE,
    enabled BOOLEAN DEFAULT TRUE,
    shared_authorization_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (shared_authorization_id) REFERENCES shared_authorizations(id) ON DELETE CASCADE
);

-- Shared authorizations table
CREATE TABLE IF NOT EXISTS shared_authorizations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    grantee_user_id INTEGER NOT NULL,
    owner_recipient_id INTEGER NOT NULL,
    permission_level TEXT NOT NULL DEFAULT 'use' CHECK (permission_level IN ('use', 'admin')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'revoked', 'declined')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP,
    FOREIGN KEY (owner_user_id) REFERENCES users(user_id),
    FOREIGN KEY (grantee_user_id) REFERENCES users(user_id),
    FOREIGN KEY (owner_recipient_id) REFERENCES recipients(id) ON DELETE CASCADE,
    UNIQUE(owner_user_id, grantee_user_id, owner_recipient_id)
);

-- Authentication requests table (new workflow)
CREATE TABLE IF NOT EXISTS auth_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requester_user_id INTEGER NOT NULL,
    target_user_id INTEGER NOT NULL,
    platform_type TEXT NOT NULL,
    recipient_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'completed', 'expired', 'cancelled')),
    expires_at TIMESTAMP NOT NULL,
    completed_recipient_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (requester_user_id) REFERENCES users(user_id),
    FOREIGN KEY (target_user_id) REFERENCES users(user_id),
    FOREIGN KEY (completed_recipient_id) REFERENCES recipients(id) ON DELETE SET NULL
);

-- OAuth states table
CREATE TABLE IF NOT EXISTS oauth_states (
    user_id INTEGER NOT NULL,
    state TEXT NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    oauth_code TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, state),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Tasks table
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    recipient_id INTEGER NOT NULL,
    platform_task_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    due_time TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (recipient_id) REFERENCES recipients(id) ON DELETE CASCADE
);

-- Indices for performance
CREATE INDEX IF NOT EXISTS idx_recipients_user_id ON recipients(user_id);
CREATE INDEX IF NOT EXISTS idx_shared_auth_grantee ON shared_authorizations(grantee_user_id, status);
CREATE INDEX IF NOT EXISTS idx_shared_auth_owner ON shared_authorizations(owner_user_id, status);
CREATE INDEX IF NOT EXISTS idx_auth_requests_target ON auth_requests(target_user_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_user_recipient ON tasks(user_id, recipient_id);

-- Triggers for updated_at
CREATE TRIGGER IF NOT EXISTS update_recipients_timestamp 
AFTER UPDATE ON recipients
BEGIN
    UPDATE recipients SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS update_shared_auth_timestamp 
AFTER UPDATE ON shared_authorizations
BEGIN
    UPDATE shared_authorizations SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS update_auth_requests_timestamp 
AFTER UPDATE ON auth_requests
BEGIN
    UPDATE auth_requests SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;