from odoo import models, fields


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    tn_order_id = fields.Char('TiendaNube Order ID', readonly=True, index=True)
    tn_instance_id = fields.Many2one('tn.instance', 'TN Store', readonly=True)
    tn_gateway = fields.Char('Gateway de Pago (TN)', readonly=True)
    tn_payment_method = fields.Char('Método de Pago (TN)', readonly=True)
