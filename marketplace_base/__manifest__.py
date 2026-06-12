{
    'name': 'Marketplace Connector Base',
    'version': '18.0.1.0.0',
    'category': 'Sales',
    'summary': 'Base común para conectores de marketplaces: log de sincronización',
    'author': 'aceleradora.la',
    'website': 'https://aceleradora.la',
    'license': 'AGPL-3',
    'depends': ['base', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'views/sync_log_views.xml',
    ],
    'installable': True,
    'application': False,
}
