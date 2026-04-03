"""
CJdropshipping API Client — Sniff Amazon
========================================
Cubre las 3 funciones que necesitas:
  1. Buscar productos por nombre → precio + días de envío
  2. Consultar stock disponible de un producto
  3. Actualizar precios en BD cuando CJ los cambia (para el cron job)

Documentación oficial: https://developers.cjdropshipping.com/api2.0/v1/
"""

import os
import requests
from datetime import datetime, timedelta

CJ_BASE = "https://developers.cjdropshipping.com/api2.0/v1"
CJ_API_KEY = os.getenv("CJ_API_KEY", "")          # Tu API key de CJdropshipping
CJ_EMAIL   = os.getenv("CJ_EMAIL", "")             # Tu email de CJ (alternativa)
CJ_PASSWORD = os.getenv("CJ_PASSWORD", "")         # Tu contraseña de CJ (alternativa)

# Token en memoria (se renueva automáticamente)
_token_cache = {
    "access_token": None,
    "expires_at": None,
    "refresh_token": None,
}


# ─── AUTENTICACIÓN ────────────────────────────────────────────────────────────

def _get_token() -> str:
    """
    Devuelve un access token válido.
    Lo renueva automáticamente si está caducado.
    """
    now = datetime.utcnow()

    # Si el token sigue vigente, reutilízalo
    if _token_cache["access_token"] and _token_cache["expires_at"]:
        if now < _token_cache["expires_at"] - timedelta(minutes=10):
            return _token_cache["access_token"]

    # Si tenemos refresh token, usarlo
    if _token_cache["refresh_token"]:
        try:
            return _refresh_token()
        except Exception:
            pass  # Si falla, pedimos uno nuevo

    # Pedir token nuevo
    return _new_token()


def _new_token() -> str:
    """Obtiene un nuevo access token con API key o email+password."""
    if CJ_API_KEY:
        payload = {"apiKey": CJ_API_KEY}
    elif CJ_EMAIL and CJ_PASSWORD:
        payload = {"email": CJ_EMAIL, "password": CJ_PASSWORD}
    else:
        raise ValueError(
            "Configura CJ_API_KEY en tus variables de entorno. "
            "Encuéntrala en: cjdropshipping.com → Account → API"
        )

    resp = requests.post(
        f"{CJ_BASE}/authentication/getAccessToken",
        json=payload,
        timeout=15
    )
    data = resp.json()
    if not data.get("result"):
        raise Exception(f"CJ Auth error: {data.get('message')}")

    _token_cache["access_token"]  = data["data"]["accessToken"]
    _token_cache["refresh_token"] = data["data"]["refreshToken"]
    _token_cache["expires_at"]    = datetime.utcnow() + timedelta(days=14)
    return _token_cache["access_token"]


def _refresh_token() -> str:
    """Renueva el access token usando el refresh token."""
    resp = requests.post(
        f"{CJ_BASE}/authentication/refreshAccessToken",
        json={"refreshToken": _token_cache["refresh_token"]},
        timeout=15
    )
    data = resp.json()
    if not data.get("result"):
        raise Exception("Refresh token caducado")

    _token_cache["access_token"]  = data["data"]["accessToken"]
    _token_cache["refresh_token"] = data["data"]["refreshToken"]
    _token_cache["expires_at"]    = datetime.utcnow() + timedelta(days=14)
    return _token_cache["access_token"]


def _headers() -> dict:
    return {
        "CJ-Access-Token": _get_token(),
        "Content-Type": "application/json",
    }


# ─── 1. BUSCAR PRODUCTOS ──────────────────────────────────────────────────────

def search_products(query: str, page: int = 1, page_size: int = 10) -> list:
    """
    Busca productos en CJ por nombre.
    Devuelve lista de productos con: pid, nombre, precio, stock, días de envío.

    Uso:
        results = search_products("scalp massage brush")
    """
    resp = requests.get(
        f"{CJ_BASE}/product/list",
        headers=_headers(),
        params={
            "productNameEn": query,
            "pageNum": page,
            "pageSize": page_size,
        },
        timeout=20
    )
    data = resp.json()
    if not data.get("result"):
        raise Exception(f"CJ search error: {data.get('message')}")

    products = data.get("data", {}).get("list", [])
    return [_parse_product(p) for p in products]


def get_product_by_pid(pid: str) -> dict:
    """
    Obtiene detalles completos de un producto por su PID de CJ.
    Incluye variantes, stock, precio y tiempo de envío.
    """
    resp = requests.get(
        f"{CJ_BASE}/product/query",
        headers=_headers(),
        params={"pid": pid},
        timeout=20
    )
    data = resp.json()
    if not data.get("result"):
        raise Exception(f"CJ product error: {data.get('message')}")

    return _parse_product(data.get("data", {}))


def _parse_product(p: dict) -> dict:
    """Normaliza la respuesta de CJ al formato que usa Sniff Amazon."""
    # Precio: CJ devuelve sellPrice o variants[0].variantSellPrice
    price = p.get("sellPrice") or 0.0
    variants = p.get("variants") or []
    if not price and variants:
        price = variants[0].get("variantSellPrice", 0.0)

    # Stock
    stock = p.get("productStock") or 0
    if not stock and variants:
        stock = sum(v.get("variantStock", 0) for v in variants)

    # Tiempo de envío estimado a USA (CJ usa shippingTime en días)
    shipping_days = _extract_shipping_days(p)

    return {
        "cj_pid":          p.get("pid", ""),
        "nombre_producto": p.get("productNameEn", p.get("productName", "")),
        "cj_price":        float(price),
        "stock":           int(stock),
        "dias_envio_cj":   shipping_days,
        "categoria":       p.get("categoryName", ""),
        "imagen":          p.get("productImage", ""),
        "url_cj":          f"https://cjdropshipping.com/product/-p-{p.get('pid','')}.html",
    }


def _extract_shipping_days(p: dict) -> int:
    """
    Extrae los días de envío estimados a USA.
    CJ no siempre devuelve este campo directamente; usamos el valor
    más conservador disponible o el default de 12 días.
    """
    # Algunos endpoints devuelven shippingTime como string "7-14"
    shipping = p.get("shippingTime", "")
    if shipping:
        try:
            # Toma el valor máximo del rango "7-14" → 14
            parts = str(shipping).replace("days", "").strip().split("-")
            return int(parts[-1].strip())
        except Exception:
            pass

    # Fallback: tiempo típico de CJPacket a USA
    return 12


# ─── 2. CONSULTAR STOCK ───────────────────────────────────────────────────────

def get_stock(pid: str) -> dict:
    """
    Devuelve el stock actual de un producto CJ.
    Útil para verificar disponibilidad antes de publicar en Amazon.

    Retorna: {"pid": ..., "stock": int, "in_stock": bool}
    """
    product = get_product_by_pid(pid)
    return {
        "pid":      product["cj_pid"],
        "stock":    product["stock"],
        "in_stock": product["stock"] > 0,
    }


# ─── 3. ACTUALIZAR PRECIOS (para el cron job) ─────────────────────────────────

def update_prices_from_cj(db, cfg: dict) -> dict:
    """
    Para cada producto en BD que tenga cj_pid,
    consulta el precio y stock actual en CJ y actualiza la BD.
    Recalcula margen y semáforo con el precio nuevo.
    Llama esto desde el cron job nocturno.

    Retorna: {"updated": N, "out_of_stock": M, "errors": [...]}
    """
    from calculations import calculate_product

    rows = db.execute(
        "SELECT * FROM products WHERE cj_pid IS NOT NULL AND status='active'"
    ).fetchall()

    updated = 0
    out_of_stock = 0
    errors = []

    for r in rows:
        try:
            product = get_product_by_pid(r["cj_pid"])

            new_cost  = product["cj_price"]
            new_stock = product["stock"]
            new_days  = product["dias_envio_cj"]

            # Si se quedó sin stock, marcarlo
            if new_stock == 0:
                db.execute(
                    "UPDATE products SET status='out_of_stock', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (r["id"],)
                )
                out_of_stock += 1
                continue

            # Recalcular con el nuevo precio de CJ
            calc = calculate_product(
                new_cost,
                r["costo_envio"],   # El envío lo seguimos usando del CSV
                new_days,
                r["bb_price"],
                r["bb_days"],
                cfg
            )

            db.execute("""
                UPDATE products SET
                    costo_producto=?, dias_envio=?,
                    precio_compra_total=?, comision_amazon=?,
                    precio_recomendado=?, margen_estimado=?,
                    margen_pct=?, semaforo=?, score=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
            """, (
                new_cost, new_days,
                calc["precio_compra_total"], calc["comision_amazon"],
                calc["precio_recomendado"], calc["margen_estimado"],
                calc["margen_pct"], calc["semaforo"], calc["score"],
                r["id"]
            ))
            updated += 1

        except Exception as e:
            errors.append(f"Producto ID {r['id']}: {str(e)}")

    db.commit()
    return {"updated": updated, "out_of_stock": out_of_stock, "errors": errors}
