from odoo import models, fields, api, _
from odoo.exceptions import UserError
import requests

class MeliItem(models.Model):
    _name = 'meli.item'
    _description = 'MercadoLibre Publication (Item)'

    name = fields.Char('Title', required=True)
    product_id = fields.Many2one('product.template', 'Product', required=True)
    instance_id = fields.Many2one('meli.instance', 'ML Account', required=True)
    meli_category_id = fields.Many2one(related='product_id.meli_category_id', store=True)
    
    meli_id = fields.Char('Meli ID (Item ID)', readonly=True)
    price = fields.Float('Price', required=True)
    available_quantity = fields.Integer('Quantity', required=True, default=1)
    
    status = fields.Selection([
        ('draft', 'Draft in Odoo'),
        ('active', 'Active'),
        ('paused', 'Paused'),
        ('closed', 'Closed'),
        ('error', 'Error')
    ], string='Status', default='draft', readonly=True)
    
    listing_type = fields.Selection([
        ('free', 'Gratuita'),
        ('gold_special', 'Clásica'),
        ('gold_pro', 'Premium')
    ], string='Listing Type', required=True, default='gold_special')
    
    permalink = fields.Char('Permalink', readonly=True)
    
    def _prepare_item_json(self):
        self.ensure_one()
        attributes = []
        for attr_val in self.product_id.meli_attribute_value_ids:
            if attr_val.value:
                attributes.append({
                    "id": attr_val.meli_attribute_id.meli_id,
                    "value_name": attr_val.value
                })
        
        # We need a dummy picture if Odoo Product doesn't have a public URL
        # For a full implementation, the image should be exposed via public controller
        pictures = [{"source": "http://mla-s2-p.mlstatic.com/968521-MLA20805195516_072016-O.jpg"}]
        
        return {
            "title": self.name,
            "category_id": self.meli_category_id.meli_id,
            "price": self.price,
            "currency_id": "ARS",
            "available_quantity": self.available_quantity,
            "buying_mode": "buy_it_now",
            "condition": "new",
            "listing_type_id": self.listing_type,
            "description": {"plain_text": self.product_id.description_sale or self.name},
            "video_id": None,
            "attributes": attributes,
            "pictures": pictures
        }
        
    def action_publish(self):
        for rec in self:
            if rec.status != 'draft':
                continue
            
            data = rec._prepare_item_json()
            url = "https://api.mercadolibre.com/items"
            headers = {
                'Authorization': f'Bearer {rec.instance_id.access_token}',
                'Content-Type': 'application/json'
            }
            
            response = requests.post(url, headers=headers, json=data)
            if response.status_code == 201:
                res = response.json()
                rec.write({
                    'meli_id': res.get('id'),
                    'status': res.get('status'),
                    'permalink': res.get('permalink'),
                })
            else:
                rec.write({'status': 'error'})
                raise UserError(_("Error posting to ML: %s") % response.text)

    def action_pause(self):
        for rec in self:
            if rec.status != 'active' or not rec.meli_id:
                continue
            url = f"https://api.mercadolibre.com/items/{rec.meli_id}"
            headers = {
                'Authorization': f'Bearer {rec.instance_id.access_token}',
                'Content-Type': 'application/json'
            }
            response = requests.put(url, headers=headers, json={'status': 'paused'})
            if response.status_code == 200:
                rec.status = 'paused'
            else:
                raise UserError(_("Error pausing item: %s") % response.text)

    def action_activate(self):
        for rec in self:
            if rec.status != 'paused' or not rec.meli_id:
                continue
            url = f"https://api.mercadolibre.com/items/{rec.meli_id}"
            headers = {
                'Authorization': f'Bearer {rec.instance_id.access_token}',
                'Content-Type': 'application/json'
            }
            response = requests.put(url, headers=headers, json={'status': 'active'})
            if response.status_code == 200:
                rec.status = 'active'
            else:
                raise UserError(_("Error activating item: %s") % response.text)

    def action_sync_price_stock(self):
        for rec in self:
            if rec.status != 'active' or not rec.meli_id:
                continue
            
            # Get Price from Instance Pricelist
            price = rec.price
            if rec.instance_id.pricelist_id:
                price = rec.instance_id.pricelist_id._get_product_price(rec.product_id, 1, False)
            
            # Get Stock from Instance Location
            stock = rec.available_quantity
            if rec.instance_id.stock_location_ids:
                quants = self.env['stock.quant'].search([
                    ('product_id', '=', rec.product_id.id),
                    ('location_id', 'child_of', rec.instance_id.stock_location_ids.ids)
                ])
                stock = sum(quants.mapped('quantity')) - sum(quants.mapped('reserved_quantity'))
                stock = max(0, int(stock))

            if price != rec.price or stock != rec.available_quantity:
                rec.price = price
                rec.available_quantity = stock
                
                # Update in ML
                url = f"https://api.mercadolibre.com/items/{rec.meli_id}"
                headers = {
                    'Authorization': f'Bearer {rec.instance_id.access_token}',
                    'Content-Type': 'application/json'
                }
                data = {
                    'price': rec.price,
                    'available_quantity': rec.available_quantity
                }
                requests.put(url, headers=headers, json=data)
                
    @api.model
    def cron_sync_price_stock(self):
        items = self.search([('status', '=', 'active')])
        items.action_sync_price_stock()

