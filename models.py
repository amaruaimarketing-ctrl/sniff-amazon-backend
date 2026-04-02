from pydantic import BaseModel
from typing import Optional

class Product(BaseModel):
    asin: Optional[str] = None
    nombre_producto: str
    url_proveedor: Optional[str] = None
    costo_producto: float
    costo_envio: float
    dias_envio: int
    sku_proveedor: Optional[str] = None
    bb_price: float = 0.0
    bb_days: int = 0

class Config(BaseModel):
    delta_precio_objetivo: float = 0.70
    delta_precio_min: float = 0.50
    delta_precio_max: float = 1.00
    margen_minimo_verde: float = 20.0
    margen_minimo_amarillo: float = 15.0
    max_dias_extra_aceptables: int = 2
    comision_amazon_global: float = 12.0

class PublishLog(BaseModel):
    product_id: int
    price_used: float
    channel: str
    note: Optional[str] = None