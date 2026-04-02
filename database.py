import sqlite3, os

DB_PATH = os.getenv("DB_PATH", "sniff_amazon.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS products (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            asin                TEXT,
            nombre_producto     TEXT NOT NULL,
            url_proveedor       TEXT,
            costo_producto      REAL NOT NULL DEFAULT 0,
            costo_envio         REAL NOT NULL DEFAULT 0,
            dias_envio          INTEGER NOT NULL DEFAULT 0,
            sku_proveedor       TEXT,
            -- Datos Amazon (placeholder hasta SP-API)
            bb_price            REAL DEFAULT 0,
            bb_days             INTEGER DEFAULT 0,
            -- Cálculos
            precio_compra_total REAL DEFAULT 0,
            comision_amazon     REAL DEFAULT 0,
            precio_recomendado  REAL DEFAULT 0,
            margen_estimado     REAL DEFAULT 0,
            margen_pct          REAL DEFAULT 0,
            semaforo            TEXT DEFAULT 'red',
            score               REAL DEFAULT 0,
            -- Estado
            status              TEXT DEFAULT 'active',
            created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS config (
            key     TEXT PRIMARY KEY,
            value   TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS publish_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id  INTEGER REFERENCES products(id),
            price_used  REAL,
            channel     TEXT,
            note        TEXT,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        );

        -- Índices útiles
        CREATE INDEX IF NOT EXISTS idx_products_semaforo ON products(semaforo);
        CREATE INDEX IF NOT EXISTS idx_products_status   ON products(status);
        CREATE INDEX IF NOT EXISTS idx_products_asin     ON products(asin);
    """)
    db.commit()
    db.close()
