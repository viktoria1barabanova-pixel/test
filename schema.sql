CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT UNIQUE NOT NULL,
    full_name TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    criticality TEXT NOT NULL,
    tag TEXT NOT NULL,
    department TEXT,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    first_response_at TEXT,
    resolved_at TEXT,
    rating INTEGER,
    bitrix_sync_status TEXT DEFAULT 'pending',
    bitrix_payload TEXT,
    bitrix_entity_type TEXT,
    bitrix_entity_id INTEGER,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL,
    author_type TEXT NOT NULL,
    author_name TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read_by_client INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(ticket_id) REFERENCES tickets(id)
);
