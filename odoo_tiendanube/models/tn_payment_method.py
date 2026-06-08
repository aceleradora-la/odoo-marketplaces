from odoo import models, fields, api


class TnPaymentMethod(models.Model):
    """
    Maps a TiendaNube payment gateway to an Odoo accounting journal.
    One record per gateway per store instance.

    TiendaNube sends `gateway` and `payment_details.method` in each order.
    Examples of gateway values: mercadopago, paypal, offline, transferencia,
    todopago, mobbex, getnet, macro_click, etc.
    """
    _name = 'tn.payment.method'
    _description = 'TiendaNube Payment Method Mapping'
    _order = 'instance_id, gateway_name'

    instance_id = fields.Many2one('tn.instance', 'Store', required=True, ondelete='cascade')
    gateway_name = fields.Char(
        'Gateway TiendaNube',
        required=True,
        help='Valor exacto del campo "gateway" que devuelve TiendaNube en el pedido. '
             'Ejemplos: mercadopago, paypal, offline, transferencia, todopago, mobbex.',
    )
    payment_method_name = fields.Char(
        'Método de Pago (detalle)',
        help='Opcional. Valor del campo payment_details.method para mayor especificidad. '
             'Ejemplos: credit_card, debit_card, ticket, bank_transfer. '
             'Si se deja vacío, aplica a cualquier método del gateway.',
    )
    journal_id = fields.Many2one(
        'account.journal',
        'Diario Contable',
        required=True,
        domain="[('type', 'in', ['bank', 'cash'])]",
        help='Diario de Odoo donde se registrará el pago cuando llegue este gateway.',
    )
    description = fields.Char('Descripción', help='Referencia interna, ej: "Tarjeta de crédito vía MP"')

    _sql_constraints = [
        (
            'unique_gateway_per_instance',
            'UNIQUE(instance_id, gateway_name, payment_method_name)',
            'Ya existe un mapeo para este gateway y método en esta tienda.',
        )
    ]
