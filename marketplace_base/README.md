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
