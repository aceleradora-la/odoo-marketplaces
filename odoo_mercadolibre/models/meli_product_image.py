from odoo import models, fields, api

class MeliProductImage(models.Model):
    _name = 'meli.product.image'
    _description = 'MercadoLibre Product Image'
    _order = 'sequence, id'

    name = fields.Char('Name')
    sequence = fields.Integer('Sequence', default=10)
    image_1920 = fields.Image('Image', required=True)
    product_tmpl_id = fields.Many2one('product.template', 'Product Template', ondelete='cascade')
