# Marketplace Connector Base

Base común para los conectores de marketplaces de Odoo
(`odoo_mercadolibre`, `odoo_tiendanube`). Compatible con Odoo 18/19,
Community y Enterprise.

## Funcionalidades

### Log de Sincronización (*Marketplaces → Log de Sincronización*)
- Registro de todos los eventos de los conectores: webhooks recibidos, pedidos
  creados, productos sin matchear, pagos fallidos, errores de API
- Filtros por canal, operación y estado (errores primero por defecto)
- Botón **Reintentar** en eventos de pedidos: reprocesa el payload original
  guardado sin esperar una nueva notificación del marketplace
- Botón **Ver Documento** para saltar al pedido/registro relacionado
- Cron diario que limpia los registros exitosos de más de 30 días

### Notificaciones a Acelio (*Marketplaces → Configuración → Notificaciones a Acelio*)

Al validar una entrega de un pedido que vino de un marketplace, Odoo le avisa a
Acelio en el momento, para que informe el despacho al canal sin esperar la
sincronización periódica.

**Configuración (3 valores que da Acelio):** en Acelio, *Sync → Configuración →
la conexión de Tiendanube → “Aviso de entregas desde Odoo”*, copiá la **URL del
webhook**, la **cuenta** y el **secreto**, pegalos acá y tocá **Probar conexión**.

**Cómo funciona**
- Se dispara en `stock.picking._action_done()` — el único punto por el que pasan
  todos los caminos que dejan un picking en `done` (`state` es calculado, así que
  `write()` no lo ve; `button_validate()` puede devolver un wizard sin terminar).
- Solo para pickings **salientes** cuyo `sale_order.client_order_ref` empieza con
  el prefijo que escribe Acelio (`ML ` / `TN `). El resto de las entregas no
  genera tráfico.
- El POST sale **después del commit** (`cr.postcommit`) con timeout de 5s: la
  validación de la entrega nunca se bloquea ni se rompe si Acelio no responde.
  El resultado queda en el Log de Sincronización (operación *Notificación de envío*).

**Payload** (JSON compacto con claves ordenadas — es exactamente lo que se firma):

```json
{"client_order_ref":"TN 1234567","picking_id":42,"picking_name":"WH/OUT/00012",
 "sale_order_id":17,"tenant":"mi-cuenta","tracking_ref":"AR123456789",
 "validated_at":"2026-07-31T14:05:22Z"}
```

**Firma:** header `X-Acelio-Signature` = HMAC-SHA256 hexadecimal del cuerpo crudo
usando el secreto. El secreto nunca viaja en el pedido.

## Uso desde otros módulos

```python
self.env['marketplace.sync.log'].log_event(
    'meli',                 # canal: 'meli' | 'tiendanube'
    'order_webhook',        # operación
    'error',                # estado: 'success' | 'error'
    instance_name=self.name,
    reference=order_id,
    message='Detalle del error',
    payload=order_dict,     # se guarda como JSON para el reintento
    res_model='sale.order', res_id=so.id,
)
```

`log_event` nunca lanza excepciones: el logging no puede romper el flujo principal.

---

Desarrollado por [**aceleradora.la**](https://aceleradora.la) — Licencia AGPL-3
