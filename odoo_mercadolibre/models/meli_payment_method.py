from odoo import models, fields


class MeliPaymentMethod(models.Model):
    """
    Maps a MercadoLibre payment method to an Odoo accounting journal.

    ML order payload: payments[].payment_method_id (e.g. "visa", "account_money")
                      payments[].payment_type_id   (e.g. "credit_card", "account_money")

    Lookup order when processing an order:
      1. payment_method_id exact match (most specific)
      2. payment_type_id match (catch-all per type)
      3. Fallback: first bank/cash journal
    """
    _name = 'meli.payment.method'
    _description = 'MercadoLibre Payment Method Mapping'
    _order = 'instance_id, payment_method_id'

    instance_id = fields.Many2one('meli.instance', 'ML Account', required=True, ondelete='cascade')
    payment_method_id = fields.Char(
        'Método de Pago (ML ID)',
        help='ID exacto devuelto por ML. Ejemplos: visa, master, amex, account_money, '
             'rapipago, pagofacil, mercadopago. Dejá vacío para mapear por tipo.',
    )
    payment_type_id = fields.Char(
        'Tipo de Pago (ML)',
        help='Tipo agrupador de ML. Ejemplos: credit_card, debit_card, ticket, '
             'account_money, digital_wallet. Usado si no hay mapeo por método específico.',
    )
    journal_id = fields.Many2one(
        'account.journal',
        'Diario Contable',
        required=True,
        domain="[('type', 'in', ['bank', 'cash'])]",
    )
    description = fields.Char('Descripción', help='Referencia interna, ej: "Tarjeta de crédito Visa"')

    _sql_constraints = [
        (
            'unique_method_per_instance',
            'UNIQUE(instance_id, payment_method_id, payment_type_id)',
            'Ya existe un mapeo para este método/tipo en esta cuenta.',
        )
    ]
