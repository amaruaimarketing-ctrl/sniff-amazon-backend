import sys, os
sys.path.insert(0, os.path.dirname(__file__))
"""
Sniff Amazon – Backend (FastAPI + SQLite)
Endpoints documentados al final del archivo en ENDPOINTS.md
"""
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import csv, io, json, os
from database import init_db, get_db
from models import Product, Config, PublishLog
from calculations import calculate_product
from datetime import datetime

app = FastAPI(title="Sniff Amazon API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # En producción: reemplaza con tu dominio
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup():
    init_db()

# ─────────────────────────────────────────────
# PRODUCTOS
# ─────────────────────────────────────────────

@app.get("/api/products")
def list_products():
    """Devuelve todos los productos con cálculos ya hechos."""
    db = get_db()
    rows = db.execute("SELECT * FROM products ORDER BY score DESC").fetchall()
    db.close()
    return [dict(r) for r in rows]


@app.get("/api/products/{product_id}")
def get_product(product_id: int):
    db = get_db()
    row = db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    db.close()
    if not row:
        raise HTTPException(404, "Producto no encontrado")
    return dict(row)


@app.delete("/api/products/{product_id}")
def delete_product(product_id: int):
    db = get_db()
    db.execute("DELETE FROM products WHERE id=?", (product_id,))
    db.commit()
    db.close()
    return {"ok": True}


@app.post("/api/products/{product_id}/ignore")
def ignore_product(product_id: int):
    db = get_db()
    db.execute("UPDATE products SET status='ignored' WHERE id=?", (product_id,))
    db.commit()
    db.close()
    return {"ok": True}


# ─────────────────────────────────────────────
# IMPORTAR CSV
# ─────────────────────────────────────────────

@app.post("/api/import-csv")
async def import_csv(file: UploadFile = File(...)):
    """
    Lee un CSV del proveedor e inserta/actualiza productos en la BD.
    Columnas: asin, nombre_producto, url_proveedor, costo_producto,
              costo_envio, dias_envio, sku_proveedor,
              bb_price (opcional), bb_days (opcional)
    """
    content = await file.read()
    text = content.decode("utf-8-sig")  # utf-8-sig maneja BOM de Excel
    reader = csv.DictReader(io.StringIO(text))

    db = get_db()
    cfg = _load_config(db)
    imported = 0
    errors = []

    for i, row in enumerate(reader, start=2):  # fila 2 = primera de datos
        try:
            name    = row.get("nombre_producto", "").strip()
            asin    = row.get("asin", "").strip() or None
            url     = row.get("url_proveedor", "").strip()
            sku     = row.get("sku_proveedor", "").strip()
            cost    = float(row.get("costo_producto", 0))
            ship    = float(row.get("costo_envio", 0))
            days    = int(row.get("dias_envio", 0))
            # bb_price / bb_days: placeholder hasta tener SP-API real
            bb_price = float(row.get("bb_price", 0) or 0)
            bb_days  = int(row.get("bb_days", 0) or 0)

            if not name:
                errors.append(f"Fila {i}: nombre_producto vacío")
                continue

            calc = calculate_product(cost, ship, days, bb_price, bb_days, cfg)

            existing = db.execute(
                "SELECT id FROM products WHERE asin=? AND asin IS NOT NULL", (asin,)
            ).fetchone() if asin else None

            if existing:
                db.execute("""
                    UPDATE products SET
                        nombre_producto=?, url_proveedor=?, costo_producto=?,
                        costo_envio=?, dias_envio=?, sku_proveedor=?,
                        bb_price=?, bb_days=?,
                        precio_compra_total=?, comision_amazon=?,
                        precio_recomendado=?, margen_estimado=?,
                        margen_pct=?, semaforo=?, score=?,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                """, (
                    name, url, cost, ship, days, sku,
                    bb_price, bb_days,
                    calc["precio_compra_total"], calc["comision_amazon"],
                    calc["precio_recomendado"], calc["margen_estimado"],
                    calc["margen_pct"], calc["semaforo"], calc["score"],
                    existing["id"]
                ))
            else:
                db.execute("""
                    INSERT INTO products
                        (asin, nombre_producto, url_proveedor, costo_producto,
                         costo_envio, dias_envio, sku_proveedor,
                         bb_price, bb_days,
                         precio_compra_total, comision_amazon,
                         precio_recomendado, margen_estimado, margen_pct,
                         semaforo, score, status)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active')
                """, (
                    asin, name, url, cost, ship, days, sku,
                    bb_price, bb_days,
                    calc["precio_compra_total"], calc["comision_amazon"],
                    calc["precio_recomendado"], calc["margen_estimado"],
                    calc["margen_pct"], calc["semaforo"], calc["score"]
                ))
            imported += 1
        except Exception as e:
            errors.append(f"Fila {i}: {str(e)}")

    db.commit()
    db.close()
    return {"imported": imported, "errors": errors}


# ─────────────────────────────────────────────
# RECALCULAR (simula el cron job)
# ─────────────────────────────────────────────

@app.post("/api/recalculate")
def recalculate_all():
    """
    Recalcula márgenes y semáforos para todos los productos.
    En producción esto lo lanzaría un cron job de madrugada,
    o se llamaría después de actualizar bb_price/bb_days vía SP-API.
    """
    db = get_db()
    cfg = _load_config(db)
    rows = db.execute("SELECT * FROM products WHERE status='active'").fetchall()
    updated = 0
    for r in rows:
        calc = calculate_product(
            r["costo_producto"], r["costo_envio"], r["dias_envio"],
            r["bb_price"], r["bb_days"], cfg
        )
        db.execute("""
            UPDATE products SET
                precio_compra_total=?, comision_amazon=?,
                precio_recomendado=?, margen_estimado=?,
                margen_pct=?, semaforo=?, score=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (
            calc["precio_compra_total"], calc["comision_amazon"],
            calc["precio_recomendado"], calc["margen_estimado"],
            calc["margen_pct"], calc["semaforo"], calc["score"],
            r["id"]
        ))
        updated += 1
    db.commit()
    db.close()
    return {"recalculated": updated, "timestamp": datetime.utcnow().isoformat()}


# ─────────────────────────────────────────────
# PUBLICAR EN AMAZON
# ─────────────────────────────────────────────

@app.post("/api/products/{product_id}/publish")
def publish_product(product_id: int, body: dict = {}):
    """
    Marca el producto como 'aprobado para publicar' y registra el log.
    TODO: llamar a SP-API / AutoDS aquí cuando estén configurados.
    """
    db = get_db()
    product = db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    if not product:
        db.close()
        raise HTTPException(404, "Producto no encontrado")

    price = body.get("price", product["precio_recomendado"])

    db.execute("UPDATE products SET status='approved', updated_at=CURRENT_TIMESTAMP WHERE id=?", (product_id,))
    db.execute("""
        INSERT INTO publish_log (product_id, price_used, channel, note)
        VALUES (?, ?, 'pending_sp_api', 'Aprobado para publicar — SP-API pendiente')
    """, (product_id, price))
    db.commit()

    # ── Aquí irá la integración real ──────────────────────────────────────
    # from sp_api_client import create_or_update_listing
    # create_or_update_listing(
    #     asin=product["asin"],
    #     price=price,
    #     quantity=10,
    #     shipping_days=product["dias_envio"]
    # )
    # ─────────────────────────────────────────────────────────────────────

    db.close()
    return {
        "ok": True,
        "product_id": product_id,
        "price_used": price,
        "status": "approved",
        "note": "Cuando configures SP-API, este endpoint publicará automáticamente."
    }


# ─────────────────────────────────────────────
# EXPORTAR / PLANTILLA
# ─────────────────────────────────────────────

@app.get("/api/template-csv")
def download_template():
    """Genera la plantilla CSV vacía para importar productos."""
    headers = ["asin","nombre_producto","url_proveedor","costo_producto",
               "costo_envio","dias_envio","sku_proveedor","bb_price","bb_days"]
    example = ["B08XYZ1234","Auriculares Bluetooth Sport",
               "https://proveedor.com/producto",
               "8.50","2.70","7","SKU-001","28.99","10"]

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    w.writerow(example)
    buf.seek(0)

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=plantilla_sniff_amazon.csv"}
    )


@app.get("/api/export-autods")
def export_autods():
    """
    Exporta CSV para AutoDS con los productos aprobados.
    Columnas: BuyID, Title, Price, ASIN
    """
    db = get_db()
    rows = db.execute(
        "SELECT * FROM products WHERE status='approved'"
    ).fetchall()
    db.close()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["BuyID", "Title", "Price", "ASIN"])
    for r in rows:
        w.writerow([
            r["url_proveedor"] or "",
            r["nombre_producto"],
            f"{r['precio_recomendado']:.2f}",
            r["asin"] or ""
        ])
    buf.seek(0)

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=autods_export.csv"}
    )


# ─────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────

@app.get("/api/config")
def get_config():
    db = get_db()
    cfg = _load_config(db)
    db.close()
    return cfg


@app.put("/api/config")
def update_config(body: dict):
    db = get_db()
    allowed = ["delta_precio_objetivo","delta_precio_min","delta_precio_max",
               "margen_minimo_verde","margen_minimo_amarillo",
               "max_dias_extra_aceptables","comision_amazon_global"]
    for key, val in body.items():
        if key in allowed:
            db.execute(
                "INSERT INTO config (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=?",
                (key, str(val), str(val))
            )
    db.commit()
    db.close()
    return {"ok": True}


# ─────────────────────────────────────────────
# ESTADÍSTICAS (para el dashboard)
# ─────────────────────────────────────────────

@app.get("/api/stats")
def get_stats():
    db = get_db()
    total  = db.execute("SELECT COUNT(*) FROM products WHERE status='active'").fetchone()[0]
    green  = db.execute("SELECT COUNT(*) FROM products WHERE semaforo='green' AND status='active'").fetchone()[0]
    amber  = db.execute("SELECT COUNT(*) FROM products WHERE semaforo='amber' AND status='active'").fetchone()[0]
    red    = db.execute("SELECT COUNT(*) FROM products WHERE semaforo='red'   AND status='active'").fetchone()[0]
    avg_m  = db.execute("SELECT AVG(margen_pct) FROM products WHERE semaforo!='red' AND status='active'").fetchone()[0]
    approved = db.execute("SELECT COUNT(*) FROM products WHERE status='approved'").fetchone()[0]
    db.close()
    return {
        "total": total, "green": green, "amber": amber, "red": red,
        "avg_margin_pct": round(avg_m or 0, 1),
        "approved": approved
    }


# ─────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────

def _load_config(db):
    rows = db.execute("SELECT key, value FROM config").fetchall()
    defaults = {
        "delta_precio_objetivo": 0.70,
        "delta_precio_min": 0.50,
        "delta_precio_max": 1.00,
        "margen_minimo_verde": 20.0,
        "margen_minimo_amarillo": 15.0,
        "max_dias_extra_aceptables": 2,
        "comision_amazon_global": 12.0,
    }
    for r in rows:
        defaults[r["key"]] = float(r["value"])
    return defaults
