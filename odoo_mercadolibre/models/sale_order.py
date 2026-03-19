from odoo import models, fields

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    meli_order_id = fields.Char('MercadoLibre Order ID', readonly=True, index=True)
    meli_instance_id = fields.Many2one('meli.instance', 'ML Account', readonly=True)
