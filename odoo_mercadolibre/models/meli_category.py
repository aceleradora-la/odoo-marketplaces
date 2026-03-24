from odoo import models, fields, api
from odoo.exceptions import UserError


class MeliCategory(models.Model):
    _name = 'meli.category'
    _description = 'MercadoLibre Category'

    name = fields.Char('Name', required=True)
    meli_id = fields.Char('Meli ID', required=True, index=True)
    parent_id = fields.Many2one('meli.category', 'Parent Category', index=True, ondelete='cascade')
    child_ids = fields.One2many('meli.category', 'parent_id', 'Children')
    is_leaf = fields.Boolean('Is Leaf', default=False)

    attribute_ids = fields.One2many('meli.attribute', 'category_id', 'Attributes')

    @api.depends('name', 'parent_id.name')
    def _compute_display_name(self):
        for rec in self:
            name = rec.name
            if rec.parent_id:
                name = f"{rec.parent_id.name} / {name}"
            rec.display_name = name

    def _get_instance(self):
        instance = self.env['meli.instance'].search([('state', '=', 'authenticated')], limit=1)
        if not instance:
            raise UserError("No hay una cuenta de MercadoLibre autenticada configurada.")
        return instance

    def action_sync_attributes(self):
        """Sync attributes for this category from ML API."""
        self.ensure_one()
        instance = self._get_instance()
        url = f"https://api.mercadolibre.com/categories/{self.meli_id}/attributes"

        response = instance._call_api('GET', url)
        if response.status_code != 200:
            raise UserError(f"Error descargando atributos: {response.text}")

        for attr in response.json():
            existing = self.env['meli.attribute'].search([
                ('meli_id', '=', attr.get('id')),
                ('category_id', '=', self.id)
            ])
            tags = attr.get('tags') or {}
            vals = {
                'name': attr.get('name'),
                'value_type': attr.get('value_type'),
                'is_required': tags.get('required', False) if isinstance(tags, dict) else False,
            }
            if existing:
                existing.write(vals)
            else:
                vals.update({'meli_id': attr.get('id'), 'category_id': self.id})
                self.env['meli.attribute'].create(vals)

    def action_sync_children(self):
        """Fetch and create subcategories for this category."""
        self.ensure_one()
        instance = self.env['meli.instance'].search([('state', '=', 'authenticated')], limit=1)
        if not instance:
            return

        url = f"https://api.mercadolibre.com/categories/{self.meli_id}"
        response = instance._call_api('GET', url)

        if response.status_code != 200:
            raise UserError(f"Error obteniendo subcategorías: {response.text}")

        data = response.json()
        self.is_leaf = len(data.get('children_categories', [])) == 0

        for child in data.get('children_categories', []):
            if not self.env['meli.category'].search([('meli_id', '=', child['id'])]):
                self.env['meli.category'].create({
                    'name': child['name'],
                    'meli_id': child['id'],
                    'parent_id': self.id,
                })
