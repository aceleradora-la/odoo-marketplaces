from odoo import models, fields


class ResPartner(models.Model):
    _inherit = 'res.partner'

    tn_customer_id = fields.Char('TiendaNube Customer ID', index=True, readonly=True)
