# odoo-marketplaces

Integraciones de Odoo con marketplaces de Latinoamérica.
Compatible con **Odoo 18 y 19**, Community y Enterprise (no usa módulos Enterprise).

| Rama | Versión Odoo |
|------|--------------|
| `18.0` | Odoo 18 |
| `19.0` | Odoo 19 |

## Módulos

### `marketplace_base`

Base común requerida por ambos conectores.
- **Log de sincronización** (menú *Marketplaces → Log de Sincronización*): todos los
  webhooks, sincronizaciones y errores quedan registrados y visibles en Odoo, con
  botón **Reintentar** para reprocesar pedidos fallidos desde el payload guardado.
- Cron diario de limpieza de logs exitosos (30 días).

### `odoo_mercadolibre`

Integración completa con MercadoLibre (8 países: AR, BR, CL, MX, UY, CO, PE, VE).

**Funcionalidades**
- OAuth 2.0 con refresh automático de tokens (cron cada 30 min)
- Publicación de productos (categorías, atributos requeridos, imágenes, tipo de publicación)
- Pedidos en tiempo real vía webhook (`/meli/webhook`) + polling de respaldo (deshabilitado por defecto)
- Diferenciación FULL vs envío propio: los pedidos FULL se despachan del almacén configurado y se validan automáticamente; los de envío propio solo reservan stock
- Facturación y registro de pago automático con mapeo método de pago → diario contable
- Subida de facturas a ML (fiscal documents)
- Sincronización de precios y stock (cron cada 1 hora), incluida convivencia
  FULL + Flex vía user-products (MLA/MLC)
- **Publicaciones con variantes**: variantes Odoo (talle/color) publicadas como
  variations de ML; configurar el *Código de Atributo ML* (COLOR, SIZE…) en cada
  atributo y SKU en cada variante
- **Envíos**: estado y tracking en el pedido, descarga de etiqueta PDF, webhook
  de cambios de estado (tópico `shipments`)
- **Comisiones**: comisión ML y costo de envío del vendedor visibles en cada
  pedido; opcionalmente factura de proveedor automática en borrador
- Mensajería post-venta al chatter del pedido

**Configuración inicial**
1. Crear una app en [MercadoLibre Developers](https://developers.mercadolibre.com).
   - Redirect URI: `https://TU-DOMINIO/meli/auth`
   - Notificaciones → URL: `https://TU-DOMINIO/meli/webhook`, tópico `orders_v2`
2. En Odoo: *MercadoLibre → Configuration → Accounts* → crear cuenta con App ID y Secret.
3. Botón **1. Get Auth URL** → autorizar → pegar código → **2. Fetch Token**.
4. Configurar: tarifa de precios, ubicaciones de stock, almacén FULL (si corresponde).
5. Botón **Importar Métodos de Pago** → asignar diario contable a cada método en la pestaña *Métodos de Pago*.
6. **Sync Categories** para traer el árbol de categorías.

### `odoo_tiendanube`

Integración con TiendaNube / Nuvemshop.

**Funcionalidades**
- OAuth 2.0 (tokens permanentes, sin vencimiento)
- Publicación de productos con variantes (precio y stock por variante)
- Pedidos en tiempo real vía webhook (`/tiendanube/webhook`): pagado, creado, cancelado
- Registro automático de webhooks desde Odoo (botón, sin configuración manual)
- Facturación y pago automático con mapeo gateway → diario contable
- Cancelación automática del pedido en Odoo si se cancela en TN
- **Tracking de envíos**: al validar la entrega en Odoo se informa el tracking a
  TN y el cliente recibe la notificación (con botón manual de reenvío)
- **Importación de catálogo**: botón para traer los productos existentes de la
  tienda a Odoo, creando atributos y variantes automáticamente
- **Categorías**: sincronización del árbol de categorías y asignación al publicar
- Sincronización de precios y stock (cron cada 1 hora)

**Configuración inicial**
1. Crear una app en el [Portal de Partners de TiendaNube](https://partners.tiendanube.com).
   - Redirect URI: `https://TU-DOMINIO/tiendanube/auth`
2. En Odoo: *TiendaNube → Configuración → Cuentas* → crear cuenta con App ID, Secret y email de contacto.
3. Botón **1. Obtener URL de Auth** → autorizar → pegar código → **2. Obtener Token**.
4. Botón **Registrar Webhooks** (registra automáticamente los 6 eventos).
5. Botón **Importar Gateways de Pago** → asignar diario contable a cada gateway.
6. Configurar tarifa de precios y ubicaciones de stock.

## Notas contables

En ambos módulos, el pago se registra por el **residual de la factura** (no por el total
del gateway), garantizando la conciliación completa. Si el total del gateway difiere
(envíos, comisiones, descuentos), la diferencia queda registrada en el log del servidor
para revisión.

## Multi-compañía

Cada cuenta (instancia) tiene su compañía asignada; los pedidos, facturas y pagos se
crean en esa compañía con el diario correspondiente.

## Licencia

AGPL-3 — aceleradora.la
