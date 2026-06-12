{
    'name': 'TiendaNube Integration',
    'version': '19.0.2.0.0',
    'category': 'Sales',
    'summary': 'Integración entre Odoo y TiendaNube: productos, variantes, precios y pedidos',
    'description': """
Integración completa con TiendaNube / Nuvemshop, desarrollada por aceleradora.la.

* OAuth 2.0 multi-tienda con tokens permanentes
* Publicación de productos con variantes y categorías
* Importación del catálogo existente de la tienda (atributos y variantes incluidos)
* Pedidos en tiempo real por webhook con registro automático de webhooks
* Facturación y pago automáticos con mapeo gateway → diario contable
* Notificación de tracking al validar la entrega en Odoo
* Sync de precios y stock por variante
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
        'views/tn_instance_views.xml',
        'views/tn_product_views.xml',
        'views/sale_order_views.xml',
        'views/res_partner_views.xml',
        'views/stock_picking_views.xml',
        'views/tn_category_views.xml',
    ],
    'installable': True,
    'application': True,
}
