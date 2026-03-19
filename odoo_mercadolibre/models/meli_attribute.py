from odoo import models, fields

class MeliAttribute(models.Model):
    _name = 'meli.attribute'
    _description = 'MercadoLibre Attribute'

    name = fields.Char('Name', required=True)
    meli_id = fields.Char('Meli ID', required=True)
    value_type = fields.Char('Value Type')
    is_required = fields.Boolean('Required')
    category_id = fields.Many2one('meli.category', 'Category', required=True, ondelete='cascade')
