from odoo import models, fields, api

class ProductTemplate(models.Model):
    _inherit = 'product.template'

    meli_category_id = fields.Many2one('meli.category', string='MercadoLibre Category', domain="[('is_leaf', '=', True)]")
    meli_attribute_value_ids = fields.One2many('meli.product.attribute.value', 'product_tmpl_id', 'MercadoLibre Attribute Values')
    meli_image_ids = fields.One2many('meli.product.image', 'product_tmpl_id', 'MercadoLibre Images')

    @api.onchange('meli_category_id')
    def _onchange_meli_category_id(self):
        # Auto-populate required attributes when category changes
        if self.meli_category_id:
            # Clear existing
            self.meli_attribute_value_ids = [(5, 0, 0)]
            
            new_lines = []
            for attr in self.meli_category_id.attribute_ids:
                if attr.is_required:
                    new_lines.append((0, 0, {
                        'meli_attribute_id': attr.id,
                        'value': '',
                    }))
            self.meli_attribute_value_ids = new_lines

class MeliProductAttributeValue(models.Model):
    _name = 'meli.product.attribute.value'
    _description = 'MercadoLibre Product Attribute Value'

    product_tmpl_id = fields.Many2one('product.template', string='Product Template', required=True, ondelete='cascade')
    meli_attribute_id = fields.Many2one('meli.attribute', string='Attribute', required=True)
    value = fields.Char('Value', required=True)
    is_required = fields.Boolean(related='meli_attribute_id.is_required', readonly=True)
