"""
Lógica de negocio central de Sniff Amazon.
Toda la estrategia de Buy Box está aquí.
"""

def calculate_product(
    costo_producto: float,
    costo_envio: float,
    dias_envio: int,
    bb_price: float,
    bb_days: int,
    cfg: dict
) -> dict:
    """
    Calcula todos los campos derivados de un producto.
    Devuelve un dict listo para guardar en BD.
    """
    delta          = cfg["delta_precio_objetivo"]
    delta_min      = cfg["delta_precio_min"]
    margen_verde   = cfg["margen_minimo_verde"]
    margen_amarillo = cfg["margen_minimo_amarillo"]
    max_dias_extra = cfg["max_dias_extra_aceptables"]
    comision_pct   = cfg["comision_amazon_global"] / 100.0

    precio_compra_total = costo_producto + costo_envio

    # Si no hay datos de Amazon todavía, no podemos calcular
    if bb_price <= 0:
        return {
            "precio_compra_total": precio_compra_total,
            "comision_amazon": 0.0,
            "precio_recomendado": 0.0,
            "margen_estimado": 0.0,
            "margen_pct": 0.0,
            "semaforo": "red",
            "score": 0.0,
        }

    precio_recomendado = max(bb_price - delta, precio_compra_total * 1.01)
    comision_amazon    = precio_recomendado * comision_pct
    margen_estimado    = precio_recomendado - precio_compra_total - comision_amazon
    margen_pct         = (margen_estimado / precio_recomendado * 100) if precio_recomendado > 0 else 0.0

    # ── Clasificación de envío ──────────────────────────
    if dias_envio <= bb_days:
        ship_class = "green"
    elif dias_envio <= bb_days + max_dias_extra:
        ship_class = "amber"
    else:
        ship_class = "red"

    # ── Clasificación de margen ─────────────────────────
    if margen_pct >= margen_verde:
        margin_class = "green"
    elif margen_pct >= margen_amarillo:
        margin_class = "amber"
    else:
        margin_class = "red"

    # ── Probabilidad Buy Box ────────────────────────────
    precio_competitivo = precio_recomendado <= bb_price - delta_min

    if (margin_class == "green" and precio_competitivo and ship_class == "green"):
        semaforo = "green"
    elif (margin_class in ("green", "amber")
          and precio_recomendado <= bb_price
          and ship_class in ("green", "amber")):
        semaforo = "amber"
    else:
        semaforo = "red"

    # ── Score numérico (para ordenar la lista) ──────────
    sem_weight = {"green": 100, "amber": 50, "red": 0}[semaforo]
    score = sem_weight + max(margen_pct, 0) * 0.5

    return {
        "precio_compra_total": round(precio_compra_total, 2),
        "comision_amazon":     round(comision_amazon, 2),
        "precio_recomendado":  round(precio_recomendado, 2),
        "margen_estimado":     round(margen_estimado, 2),
        "margen_pct":          round(margen_pct, 1),
        "semaforo":            semaforo,
        "score":               round(score, 2),
    }
