# MercadoLibre Integration para Odoo

Integración completa entre Odoo (18/19, Community y Enterprise) y MercadoLibre.
Soporta los 8 sites de Latinoamérica: Argentina, Brasil, Chile, México, Uruguay,
Colombia, Perú y Venezuela.

## Funcionalidades

### Autenticación
- OAuth 2.0 con múltiples cuentas (multi-tenant)
- Refresh automático de tokens (cron cada 30 minutos + reintento ante 401)

### Publicaciones
- Publicación de productos con categorías, atributos requeridos e imágenes
- Tipos de publicación: Gratuita, Clásica y Premium
- Condición, modo de envío, envío gratis y retiro en persona configurables
- **Variantes**: productos con talle/color publicados como variations de ML
  (configurar el *Código de Atributo ML* en cada atributo y SKU en cada variante)
- Validación pre-publicación con errores claros
- Importación de publicaciones existentes desde ML

### Pedidos (tiempo real)
- Webhook `/meli/webhook` (tópicos `orders_v2` y `shipments`)
- Diferenciación **FULL vs envío propio**: los pedidos FULL se despachan del
  almacén configurado y se validan automáticamente; los de envío propio
  reservan stock hasta el despacho físico
- Factura y pago automáticos con **mapeo método de pago → diario contable**
- Datos del comprador (email, teléfono, dirección) en el partner
- Polling de respaldo (cron, desactivado por defecto)

### Envíos
- Estado, tracking y tipo de logística en el pedido
- Descarga de **etiqueta de envío PDF** desde Odoo

### Contabilidad
- Comisión ML y costo de envío del vendedor visibles en cada pedido
- Factura de proveedor automática (opcional) con esos costos
- Subida de facturas a ML (fiscal documents)

### Stock
- Sync de precios y stock cada hora (por publicación y por variación)
- **Convivencia FULL + Flex** (MLA/MLC): stock del depósito propio gestionado
  vía user-products con header x-version y reintento ante conflictos

## Configuración rápida

1. Crear una app en [MercadoLibre Developers](https://developers.mercadolibre.com)
   - Redirect URI: `https://TU-DOMINIO/meli/auth`
   - Notificaciones: `https://TU-DOMINIO/meli/webhook` — tópicos `orders_v2` y `shipments`
2. *MercadoLibre → Configuration → Accounts*: crear cuenta, autorizar y obtener token
3. Configurar tarifa, ubicaciones de stock, almacén FULL y mapeo de métodos de pago
   (botón **Importar Métodos de Pago**)
4. Sincronizar categorías y publicar

Los errores de sincronización quedan visibles en *Marketplaces → Log de Sincronización*.

---

Desarrollado por [**aceleradora.la**](https://aceleradora.la) — Licencia AGPL-3
