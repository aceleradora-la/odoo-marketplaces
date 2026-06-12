# TiendaNube Integration para Odoo

Integración entre Odoo (18/19, Community y Enterprise) y TiendaNube / Nuvemshop:
productos, variantes, precios, stock, pedidos y envíos.

## Funcionalidades

### Autenticación
- OAuth 2.0 con tokens permanentes (sin vencimiento ni refresh)
- Múltiples tiendas (multi-tenant)

### Productos
- Publicación de productos con **variantes** (precio y stock por variante)
- **Importación del catálogo existente**: trae los productos de la tienda a Odoo
  creando atributos y variantes automáticamente
- **Categorías**: sincronización del árbol de la tienda y asignación al publicar
- Sync de precios y stock cada hora

### Pedidos (tiempo real)
- Webhooks registrados automáticamente desde Odoo con un botón
  (pagado, creado, cancelado, productos)
- Factura y pago automáticos con **mapeo gateway → diario contable**
  (botón **Importar Gateways de Pago** para traerlos de la tienda)
- Cancelación automática del pedido en Odoo si se cancela en TN
- Datos del cliente (email, teléfono, dirección, país) en el partner
- Polling de respaldo (cron, desactivado por defecto)

### Envíos
- Al validar la entrega en Odoo se informa el **tracking** a TiendaNube y el
  cliente recibe la notificación de envío (con botón de reenvío manual)
- Compatible con tiendas en la API de fulfillments nueva y la antigua

## Configuración rápida

1. Crear una app en el [Portal de Partners de TiendaNube](https://partners.tiendanube.com)
   - Redirect URI: `https://TU-DOMINIO/tiendanube/auth`
2. *TiendaNube → Configuración → Cuentas*: crear cuenta, autorizar y obtener token
3. Botón **Registrar Webhooks** (automático, sin configuración manual)
4. **Importar Gateways de Pago** y asignar diarios contables
5. **Sincronizar Categorías** e **Importar Catálogo** (si la tienda ya tiene productos)

Los errores de sincronización quedan visibles en *Marketplaces → Log de Sincronización*.

---

Desarrollado por [**aceleradora.la**](https://aceleradora.la) — Licencia AGPL-3
