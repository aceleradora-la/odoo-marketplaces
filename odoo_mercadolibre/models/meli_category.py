from odoo import models, fields, api
import requests

class MeliCategory(models.Model):
    _name = 'meli.category'
    _description = 'MercadoLibre Category'

    name = fields.Char('Name', required=True)
    meli_id = fields.Char('Meli ID', required=True, index=True)
    parent_id = fields.Many2one('meli.category', 'Parent Category', index=True, ondelete='cascade')
    child_ids = fields.One2many('meli.category', 'parent_id', 'Children')
    is_leaf = fields.Boolean('Is Leaf', default=False)
    
    attribute_ids = fields.One2many('meli.attribute', 'category_id', 'Attributes')

    def name_get(self):
        result = []
        for rec in self:
            name = rec.name
            if rec.parent_id:
                name = f"{rec.parent_id.name} / {name}"
            result.append((rec.id, name))
        return result

    def action_sync_attributes(self):
        """
        Sync attributes for this category from ML API
        GET /categories/{category_id}/attributes
        """
        self.ensure_one()
        instance = self.env['meli.instance'].search([('state', '=', 'authenticated')], limit=1)
        if not instance:
            return
            
        url = f"https://api.mercadolibre.com/categories/{self.meli_id}/attributes"
        headers = {'Authorization': f'Bearer {instance.access_token}'}
        
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            for attr in data:
                existing = self.env['meli.attribute'].search([
                    ('meli_id', '=', attr.get('id')),
                    ('category_id', '=', self.id)
                ])
                vals = {
                    'name': attr.get('name'),
                    'value_type': attr.get('value_type'),
                    'is_required': 'REQUIRED' in (attr.get('tags') or {}),
                }
                if existing:
                    existing.write(vals)
                else:
                    vals.update({
                        'meli_id': attr.get('id'),
                        'category_id': self.id,
                    })
                    self.env['meli.attribute'].create(vals)

    def action_sync_children(self):
        self.ensure_one()
        instance = self.env['meli.instance'].search([('state', '=', 'authenticated')], limit=1)
        if not instance:
            return
            
        url = f"https://api.mercadolibre.com/categories/{self.meli_id}"
        headers = {'Authorization': f'Bearer {instance.access_token}'}
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            # Meli returns if category is leaf or not in 'children_categories' (empty if leaf)
            self.is_leaf = len(data.get('children_categories', [])) == 0
            
            for child in data.get('children_categories', []):
                existing = self.env['meli.category'].search([('meli_id', '=', child['id'])])
                if not existing:
                    self.env['meli.category'].create({
                        'name': child['name'],
                        'meli_id': child['id'],
                        'parent_id': self.id,
                    })
