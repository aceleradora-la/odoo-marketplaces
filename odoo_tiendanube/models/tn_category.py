from odoo import models, fields, api


class TnCategory(models.Model):
    _name = 'tn.category'
    _description = 'TiendaNube Category'
    _order = 'name'

    name = fields.Char('Nombre', required=True)
    tn_category_id = fields.Char('TN Category ID', required=True, index=True)
    parent_id = fields.Many2one('tn.category', 'Categoría Padre', index=True, ondelete='cascade')
    child_ids = fields.One2many('tn.category', 'parent_id', 'Subcategorías')
    instance_id = fields.Many2one('tn.instance', 'Tienda', required=True, ondelete='cascade')

    _sql_constraints = [
        ('unique_tn_category_per_instance',
         'UNIQUE(instance_id, tn_category_id)',
         'La categoría ya existe para esta tienda.'),
    ]

    @api.depends('name', 'parent_id.name')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = (
                f"{rec.parent_id.name} / {rec.name}" if rec.parent_id else rec.name
            )
