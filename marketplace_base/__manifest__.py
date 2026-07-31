{
    'name': 'Marketplace Connector Base',
    'version': '19.0.1.1.0',
    'category': 'Sales',
    'summary': 'Base común para conectores de marketplaces: log de sincronización',
    'description': """
Base común para los conectores de marketplaces de aceleradora.la.

* Log de sincronización visible en Odoo con filtros por canal/operación/estado
* Reintento de pedidos fallidos desde el payload original guardado
* Limpieza automática de registros antiguos
    """,
    'author': 'aceleradora.la',
    'maintainer': 'aceleradora.la',
    'website': 'https://aceleradora.la',
    'license': 'AGPL-3',
    'depends': ['base', 'mail', 'product'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'views/sync_log_views.xml',
    ],
    'installable': True,
    'application': False,
}

