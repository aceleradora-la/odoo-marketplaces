from odoo import models, fields


class ProductAttribute(models.Model):
    _inherit = 'product.attribute'

    meli_attribute_code = fields.Char(
        'Código de Atributo ML',
        help='ID del atributo en MercadoLibre usado en variaciones, ej: COLOR, SIZE. '
             'Requerido para publicar productos con variantes.',
    )
