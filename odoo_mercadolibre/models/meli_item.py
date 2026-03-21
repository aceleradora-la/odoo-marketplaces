from odoo import models, fields, api, _
from odoo.exceptions import UserError
import requests
import logging

_logger = logging.getLogger(__name__)

class MeliItem(models.Model):
    _name = 'meli.item'
    _description = 'MercadoLibre Publication (Item)'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char('Title', required=True, tracking=True)
    product_id = fields.Many2one('product.template', 'Product', required=True, tracking=True)
    instance_id = fields.Many2one('meli.instance', 'ML Account', required=True, tracking=True)
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
    ], string='Status', default='draft', readonly=True, tracking=True)
    
    listing_type = fields.Selection([
        ('free', 'Gratuita'),
        ('gold_special', 'Clásica'),
        ('gold_pro', 'Premium')
    ], string='Listing Type', required=True, default='gold_special', tracking=True)

    def action_back_to_draft(self):
        self.ensure_one()
        self.write({'status': 'draft'})
        self.message_post(body="Se ha reseteado el estado a borrador.")
    
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
        
        # Get base URL for images
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        
        # Prepare pictures (Main image + Extra ML images)
        pictures = []
        # Main Odoo Product Image
        if self.product_id.image_1920:
             pictures.append({
                 'source': f"{base_url}/meli_image/product.template/{self.product_id.id}/image_1920"
             })
        
        # Extra ML Images (new model)
        for img in self.product_id.meli_image_ids:
            pictures.append({
                'source': f"{base_url}/meli_image/meli.product.image/{img.id}/image_1920"
            })

        return {
            'title': self.name,
            'category_id': self.meli_category_id.meli_id,
            'price': self.price,
            'currency_id': 'ARS',  # Could be dynamic
            'available_quantity': self.available_quantity,
            'buying_mode': 'buy_it_now',
            'listing_type_id': self.listing_type,
            'condition': 'new',
            'description': {"plain_text": self.product_id.description_sale or self.name},
            'video_id': None,
            'pictures': pictures,
            'attributes': attributes,
            "shipping": {
                "mode": "me2",
                "local_pick_up": True,
                "free_shipping": False,
            }
        }
        
    def action_publish(self):
        for rec in self:
            if rec.status != 'draft':
                continue
            
            # Ensure token is valid before starting
            try:
                rec.instance_id.check_token_validity()
            except Exception as e:
                rec.message_post(body=f"Error validando token: {str(e)}")
                raise UserError(_("Could not validate token: %s") % str(e))

            rec.message_post(body="Iniciando publicación en MercadoLibre...")
            data = rec._prepare_item_json()
            url = "https://api.mercadolibre.com/items"
            headers = {
                'Authorization': f'Bearer {rec.instance_id.access_token}',
                'Content-Type': 'application/json'
            }
            
            try:
                response = requests.post(url, headers=headers, json=data)
                if response.status_code == 201:
                    res = response.json()
                    rec.write({
                        'meli_id': res.get('id'),
                        'status': res.get('status'),
                        'permalink': res.get('permalink'),
                    })
                    rec.message_post(body=f"Publicado exitosamente. ML ID: {res.get('id')}")
                    _logger.info(f"Successfully published item {rec.name} to MercadoLibre")
                else:
                    error_data = response.text
                    rec.write({'status': 'error'})
                    rec.message_post(body=f"Error al publicar en ML: {error_data}")
                    _logger.error(f"Error publishing {rec.name} to ML: {error_data}")
                    # No raise here to allow user to see error in chatter
            except Exception as e:
                error_msg = str(e)
                rec.write({'status': 'error'})
                rec.message_post(body=f"Excepción durante la publicación: {error_msg}")
                _logger.error(f"Exception while publishing item {rec.name}: {error_msg}")

    def action_pause(self):
        for rec in self:
            if rec.status != 'active' or not rec.meli_id:
                continue
            
            rec.instance_id.check_token_validity()
            url = f"https://api.mercadolibre.com/items/{rec.meli_id}"
            headers = {
                'Authorization': f'Bearer {rec.instance_id.access_token}',
                'Content-Type': 'application/json'
            }
            try:
                response = requests.put(url, headers=headers, json={'status': 'paused'})
                if response.status_code == 200:
                    rec.status = 'paused'
                    _logger.info(f"Successfully paused item {rec.meli_id}")
                else:
                    _logger.error(f"Error pausing item {rec.meli_id}: {response.text}")
                    raise UserError(_("Error pausing item: %s") % response.text)
            except Exception as e:
                _logger.error(f"Exception while pausing item {rec.meli_id}: {str(e)}")

    def action_activate(self):
        for rec in self:
            if rec.status != 'paused' or not rec.meli_id:
                continue
            
            rec.instance_id.check_token_validity()
            url = f"https://api.mercadolibre.com/items/{rec.meli_id}"
            headers = {
                'Authorization': f'Bearer {rec.instance_id.access_token}',
                'Content-Type': 'application/json'
            }
            try:
                response = requests.put(url, headers=headers, json={'status': 'active'})
                if response.status_code == 200:
                    rec.status = 'active'
                    _logger.info(f"Successfully activated item {rec.meli_id}")
                else:
                    _logger.error(f"Error activating item {rec.meli_id}: {response.text}")
                    raise UserError(_("Error activating item: %s") % response.text)
            except Exception as e:
                _logger.error(f"Exception while activating item {rec.meli_id}: {str(e)}")

    def action_sync_price_stock(self):
        for rec in self:
            if rec.status != 'active' or not rec.meli_id:
                continue
            
            rec.instance_id.check_token_validity()
            
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
                try:
                    response = requests.put(url, headers=headers, json=data)
                    if response.status_code == 200:
                        _logger.info(f"Successfully synced price and stock for item {rec.meli_id}")
                    else:
                        _logger.error(f"Failed to sync price/stock for {rec.meli_id}: {response.text}")
                except Exception as e:
                    _logger.error(f"Exception syncing price/stock for {rec.meli_id}: {str(e)}")
                
    @api.model
    def action_import_items(self, instance):
        """
        Fetch all items for the seller and create/update meli.item records.
        Using /users/{user_id}/items/search
        """
        instance.check_token_validity()
        if not instance.seller_id:
            # Try to get seller_id
            headers = {'Authorization': f'Bearer {instance.access_token}'}
            user_response = requests.get("https://api.mercadolibre.com/users/me", headers=headers)
            if user_response.status_code == 200:
                instance.seller_id = str(user_response.json().get('id'))
            else:
                raise UserError(_("Could not fetch seller ID for item import."))

        url = f"https://api.mercadolibre.com/users/{instance.seller_id}/items/search"
        headers = {'Authorization': f'Bearer {instance.access_token}'}
        
        # Paginate through results
        offset = 0
        limit = 50
        while True:
            params = {'offset': offset, 'limit': limit}
            response = requests.get(url, headers=headers, params=params)
            if response.status_code != 200:
                _logger.error(f"Error searching items: {response.text}")
                break
            
            data = response.json()
            item_ids = data.get('results', [])
            if not item_ids:
                break
                
            # Fetch details for each item ID (can use multiget but limit is 20)
            for i in range(0, len(item_ids), 20):
                batch = item_ids[i:i+20]
                ids_str = ",".join(batch)
                details_url = f"https://api.mercadolibre.com/items?ids={ids_str}"
                det_resp = requests.get(details_url, headers=headers)
                if det_resp.status_code == 200:
                    for item_data_wrapper in det_resp.json():
                        # ML returns a list of {code: 200, body: {...}}
                        if item_data_wrapper.get('code') != 200:
                            continue
                        item_data = item_data_wrapper.get('body')
                        self._create_or_update_from_ml(item_data, instance)
                else:
                    _logger.error(f"Error fetching item details: {det_resp.text}")
            
            offset += limit
            if offset >= data.get('paging', {}).get('total', 0):
                break

    @api.model
    def _create_or_update_from_ml(self, data, instance):
        meli_id = data.get('id')
        existing = self.search([('meli_id', '=', meli_id)], limit=1)
        
        status_map = {
            'active': 'active',
            'paused': 'paused',
            'closed': 'closed',
            'under_review': 'paused',
            'inactive': 'paused',
        }
        
        vals = {
            'name': data.get('title'),
            'price': data.get('price'),
            'available_quantity': data.get('available_quantity'),
            'status': status_map.get(data.get('status'), 'error'),
            'permalink': data.get('permalink'),
            'instance_id': instance.id,
        }
        
        if not existing:
            # Match product or create new one
            # Try to match by SKU (seller_custom_field in ML)
            sku = data.get('seller_custom_field')
            product = False
            if sku:
                product = self.env['product.template'].search([('default_code', '=', sku)], limit=1)
            
            if not product:
                # Try match by title
                product = self.env['product.template'].search([('name', '=', data.get('title'))], limit=1)
            
            if not product:
                # Create a minimal product template
                product = self.env['product.template'].create({
                    'name': data.get('title'),
                    'list_price': data.get('price'),
                    'default_code': sku or '',
                    'type': 'consu', # or 'product' depending on stock
                })
            
            vals.update({
                'meli_id': meli_id,
                'product_id': product.id,
            })
            self.create(vals)
        else:
            existing.write(vals)

    @api.model
    def cron_sync_price_stock(self):
        items = self.search([('status', '=', 'active')])
        items.action_sync_price_stock()

