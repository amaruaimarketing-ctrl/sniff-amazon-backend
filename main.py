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
# CJ DROPSHIPPING
# ─────────────────────────────────────────────

@app.get("/api/cj/search")
def cj_search(q: str, page: int = 1):
    """
    Busca productos en CJdropshipping por nombre.
    Devuelve precio, stock y días de envío estimados.
    Ejemplo: GET /api/cj/search?q=scalp+massage+brush
    """
    try:
        from cj_client import search_products
        results = search_products(q, page=page, page_size=10)
        return {"ok": True, "results": results, "query": q}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Error CJ API: {str(e)}")


@app.get("/api/cj/product/{pid}")
def cj_product(pid: str):
    """
    Obtiene detalles completos de un producto CJ por su PID.
    Incluye precio actual, stock y días de envío.
    """
    try:
        from cj_client import get_product_by_pid
        return {"ok": True, "product": get_product_by_pid(pid)}
    except Exception as e:
        raise HTTPException(500, f"Error CJ API: {str(e)}")


@app.get("/api/cj/stock/{pid}")
def cj_stock(pid: str):
    """
    Consulta el stock actual de un producto CJ.
    Útil para verificar antes de publicar en Amazon.
    """
    try:
        from cj_client import get_stock
        return get_stock(pid)
    except Exception as e:
        raise HTTPException(500, f"Error CJ API: {str(e)}")


@app.post("/api/cj/sync-prices")
def cj_sync_prices():
    """
    Actualiza precios y stock de todos los productos que tienen cj_pid.
    El cron job nocturno llama a esto automáticamente.
    También puedes llamarlo manualmente con el botón de recalcular.
    """
    try:
        from cj_client import update_prices_from_cj
        db = get_db()
        cfg = _load_config(db)
        result = update_prices_from_cj(db, cfg)
        db.close()
        return {"ok": True, **result, "timestamp": datetime.utcnow().isoformat()}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Error sincronizando CJ: {str(e)}")


@app.post("/api/products/{product_id}/link-cj")
def link_cj_product(product_id: int, body: dict = {}):
    """
    Vincula un producto de la BD con su PID de CJdropshipping.
    Después de vincular, el cron job actualizará precios automáticamente.
    Body: {"cj_pid": "xxxxx"}
    """
    cj_pid = body.get("cj_pid", "").strip()
    if not cj_pid:
        raise HTTPException(400, "Falta cj_pid en el body")

    db = get_db()
    db.execute(
        "UPDATE products SET cj_pid=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (cj_pid, product_id)
    )
    db.commit()
    db.close()
    return {"ok": True, "product_id": product_id, "cj_pid": cj_pid}



# ─────────────────────────────────────────────
# HUNTER — Analizar ASIN de Amazon
# ─────────────────────────────────────────────

@app.get("/api/hunter/analyze")
def hunter_analyze(asin: str, bb_price: float = 0, bb_days: int = 0, search_term: str = ""):
    """
    Analiza un ASIN de Amazon:
    1. Busca el producto en CJdropshipping por nombre
    2. Calcula margen comparando CJ vs Buy Box de Amazon
    3. Devuelve semáforo y recomendación

    Params:
      asin       — ASIN de Amazon (ej: B08XYZ1234)
      bb_price   — Precio actual de la Buy Box en Amazon (lo pone el usuario)
      bb_days    — Días de envío del ganador de la Buy Box
      search_term — Nombre del producto para buscar en CJ
    """
    if not asin:
        raise HTTPException(400, "Falta el ASIN")
    if not search_term:
        raise HTTPException(400, "Falta el nombre del producto para buscar en CJ")
    if bb_price <= 0:
        raise HTTPException(400, "Ingresa el precio de la Buy Box de Amazon")

    try:
        from cj_client import search_products
        results = search_products(search_term, page_size=5)
    except Exception as e:
        raise HTTPException(500, f"Error buscando en CJ: {str(e)}")

    if not results:
        return {
            "asin": asin,
            "bb_price": bb_price,
            "bb_days": bb_days,
            "cj_results": [],
            "best": None,
            "message": "No se encontraron productos en CJ para ese nombre"
        }

    db = get_db()
    cfg = _load_config(db)
    db.close()

    analyzed = []
    for p in results:
        from calculations import calculate_product
        calc = calculate_product(
            p["cj_price"],
            0,           # costo envio: CJ lo incluye en el precio
            p["dias_envio_cj"],
            bb_price,
            bb_days,
            cfg
        )
        analyzed.append({
            **p,
            **calc,
            "asin": asin,
            "bb_price": bb_price,
            "bb_days": bb_days,
        })

    # Ordenar por score descendente
    analyzed.sort(key=lambda x: x.get("score", 0), reverse=True)
    best = analyzed[0] if analyzed else None

    return {
        "asin": asin,
        "bb_price": bb_price,
        "bb_days": bb_days,
        "best": best,
        "all_results": analyzed,
    }


@app.post("/api/hunter/save")
def hunter_save(body: dict = {}):
    """
    Guarda el mejor resultado del Hunter como producto en la BD.
    Body: resultado de /api/hunter/analyze con campo best
    """
    best = body.get("best")
    if not best:
        raise HTTPException(400, "Falta el producto a guardar")

    db = get_db()
    cfg = _load_config(db)

    from calculations import calculate_product
    calc = calculate_product(
        best.get("cj_price", 0),
        0,
        best.get("dias_envio_cj", 12),
        best.get("bb_price", 0),
        best.get("bb_days", 0),
        cfg
    )

    asin = best.get("asin", "")
    existing = db.execute(
        "SELECT id FROM products WHERE asin=? AND asin IS NOT NULL", (asin,)
    ).fetchone() if asin else None

    if existing:
        db.execute("""
            UPDATE products SET
                nombre_producto=?, costo_producto=?, dias_envio=?,
                bb_price=?, bb_days=?, cj_pid=?,
                precio_compra_total=?, comision_amazon=?,
                precio_recomendado=?, margen_estimado=?,
                margen_pct=?, semaforo=?, score=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (
            best.get("nombre_producto",""), best.get("cj_price",0), best.get("dias_envio_cj",0),
            best.get("bb_price",0), best.get("bb_days",0), best.get("cj_pid",""),
            calc["precio_compra_total"], calc["comision_amazon"],
            calc["precio_recomendado"], calc["margen_estimado"],
            calc["margen_pct"], calc["semaforo"], calc["score"],
            existing["id"]
        ))
        product_id = existing["id"]
    else:
        cur = db.execute("""
            INSERT INTO products
                (asin, nombre_producto, url_proveedor, costo_producto,
                 costo_envio, dias_envio, cj_pid,
                 bb_price, bb_days,
                 precio_compra_total, comision_amazon,
                 precio_recomendado, margen_estimado, margen_pct,
                 semaforo, score, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active')
        """, (
            asin, best.get("nombre_producto",""),
            best.get("url_cj",""), best.get("cj_price",0),
            0, best.get("dias_envio_cj",0), best.get("cj_pid",""),
            best.get("bb_price",0), best.get("bb_days",0),
            calc["precio_compra_total"], calc["comision_amazon"],
            calc["precio_recomendado"], calc["margen_estimado"],
            calc["margen_pct"], calc["semaforo"], calc["score"]
        ))
        product_id = cur.lastrowid

    db.commit()
    db.close()
    return {"ok": True, "product_id": product_id, "semaforo": calc["semaforo"]}

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
