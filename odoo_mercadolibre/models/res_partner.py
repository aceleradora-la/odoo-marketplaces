from odoo import models, fields

class ResPartner(models.Model):
    _inherit = 'res.partner'

    meli_user_id = fields.Char('MercadoLibre User ID', index=True)
    meli_nickname = fields.Char('MercadoLibre Nickname')
