{
    'name': 'MercadoLibre Integration',
    'version': '18.0.3.0.0',
    'category': 'Sales',
    'summary': 'Integración completa entre Odoo y MercadoLibre (V18/19)',
    'description': """
Integración completa con MercadoLibre (8 países de LatAm), desarrollada por aceleradora.la.

* OAuth 2.0 multi-cuenta con refresh automático de tokens
* Publicaciones con categorías, atributos, imágenes y variantes (variations)
* Pedidos en tiempo real por webhook, diferenciando FULL vs envío propio
* Facturación y pago automáticos con mapeo método de pago → diario contable
* Envíos: estado, tracking y descarga de etiqueta PDF
* Comisiones y costos de envío del vendedor, con factura de proveedor opcional
* Stock por publicación/variación, incluida convivencia FULL + Flex (user-products)
* Subida de facturas a ML y mensajería post-venta
    """,
    'author': 'aceleradora.la',
    'maintainer': 'aceleradora.la',
    'website': 'https://aceleradora.la',
    'license': 'AGPL-3',
    'depends': ['base', 'sale_management', 'stock', 'account', 'mail', 'marketplace_base'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'views/menuitem.xml',
        'views/meli_instance_views.xml',
        'views/meli_category_views.xml',
        'views/product_template_views.xml',
        'views/product_attribute_views.xml',
        'views/meli_item_views.xml',
        'views/sale_order_views.xml',
        'views/res_partner_views.xml',
    ],
    'installable': True,
    'application': True,
}
