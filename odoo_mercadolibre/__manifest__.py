{
    'name': 'MercadoLibre Integration',
    'version': '18.0.1.4.6',
    'category': 'Sales',
    'summary': 'Integración completa entre Odoo y MercadoLibre (V18/19)',
    'description': """
        Módulo base para la integración con MercadoLibre.
        Soporta múltiples cuentas (App ID), autenticación OAuth 2.0.
    """,
    'author': 'aceleradora.la',
    'website': 'https://aceleradora.la',
    'license': 'AGPL-3',
    'depends': ['base', 'sale_management', 'stock', 'account', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'views/menuitem.xml',
        'views/meli_instance_views.xml',
        'views/meli_category_views.xml',
        'views/product_template_views.xml',
        'views/meli_item_views.xml',
        'views/sale_order_views.xml',
        'views/res_partner_views.xml',
    ],
    'installable': True,
    'application': True,
}
