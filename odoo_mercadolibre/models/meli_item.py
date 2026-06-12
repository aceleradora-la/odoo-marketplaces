from odoo import models, fields, api, _
from odoo.exceptions import UserError
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

    user_product_id = fields.Char(
        'User Product ID', readonly=True,
        help='ID del user-product de ML, necesario para gestionar stock por ubicación '
             '(convivencia FULL + Flex).',
    )
    is_full_flex = fields.Boolean(
        'Convivencia FULL + Flex', readonly=True,
        help='El ítem tiene logística fulfillment y Flex activos a la vez. El stock del '
             'depósito propio (selling_address) se sincroniza vía el endpoint de user-products.',
    )

    condition = fields.Selection([
        ('new', 'Nuevo'),
        ('used', 'Usado'),
        ('refurbished', 'Reacondicionado'),
    ], string='Condición', required=True, default='new', tracking=True)
    shipping_mode = fields.Selection([
        ('me2', 'Mercado Envíos'),
        ('not_specified', 'A acordar con el comprador'),
    ], string='Modo de Envío', required=True, default='me2')
    free_shipping = fields.Boolean('Envío Gratis', default=False)
    local_pick_up = fields.Boolean('Retiro en Persona', default=True)

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
                 'source': f"{base_url}/meli_image/product.template/{self.product_id.id}/image_1920/product_main.jpg"
             })
        
        # Extra ML Images (new model)
        for img in self.product_id.meli_image_ids:
            pictures.append({
                'source': f"{base_url}/meli_image/meli.product.image/{img.id}/image_1920/extra_{img.id}.jpg"
            })

        return {
            'title': self.name,
            'category_id': self.meli_category_id.meli_id,
            'price': self.price,
            'currency_id': self.instance_id.currency_ml if self.instance_id else 'ARS',
            'available_quantity': self.available_quantity,
            'buying_mode': 'buy_it_now',
            'listing_type_id': self.listing_type,
            'condition': self.condition,
            'description': {"plain_text": self.product_id.description_sale or self.name},
            'pictures': pictures,
            'attributes': attributes,
            "shipping": {
                "mode": self.shipping_mode,
                "local_pick_up": self.local_pick_up,
                "free_shipping": self.free_shipping,
            }
        }
        
    def _validate_before_publish(self):
        """Validate the item locally before hitting the ML API, with clear errors."""
        self.ensure_one()
        errors = []
        if not self.meli_category_id:
            errors.append(_("El producto no tiene categoría de MercadoLibre asignada."))
        elif not self.meli_category_id.is_leaf:
            errors.append(_("La categoría '%s' no es una categoría hoja: elegí una subcategoría final.") % self.meli_category_id.name)
        if self.price <= 0:
            errors.append(_("El precio debe ser mayor a 0."))
        if self.available_quantity <= 0:
            errors.append(_("La cantidad disponible debe ser mayor a 0."))

        missing_attrs = self.meli_category_id.attribute_ids.filtered('is_required') if self.meli_category_id else []
        filled_attr_ids = self.product_id.meli_attribute_value_ids.filtered('value').mapped('meli_attribute_id')
        missing = [a.name for a in missing_attrs if a not in filled_attr_ids]
        if missing:
            errors.append(_("Faltan atributos requeridos: %s") % ', '.join(missing))

        if errors:
            raise UserError(_("No se puede publicar '%s':\n- %s") % (self.name, '\n- '.join(errors)))

    def action_publish(self):
        for rec in self:
            if rec.status != 'draft':
                continue

            rec._validate_before_publish()

            # Ensure token is valid before starting
            try:
                rec.instance_id.check_token_validity()
            except Exception as e:
                rec.message_post(body=f"Error validando token: {str(e)}")
                raise UserError(_("Could not validate token: %s") % str(e))

            rec.message_post(body="Iniciando publicación en MercadoLibre...")
            data = rec._prepare_item_json()
            try:
                url = "https://api.mercadolibre.com/items"
                response = rec.instance_id._call_api('POST', url, json=data)
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
            try:
                response = rec.instance_id._call_api('PUT', url, json={'status': 'paused'})
                if response.status_code == 200:
                    rec.status = 'paused'
                    rec.message_post(body="Publicación pausada en MercadoLibre.")
                    _logger.info(f"Successfully paused item {rec.meli_id}")
                else:
                    _logger.error(f"Error pausing item {rec.meli_id}: {response.text}")
                    raise UserError(_("Error pausing item: %s") % response.text)
            except UserError:
                raise
            except Exception as e:
                _logger.error(f"Exception while pausing item {rec.meli_id}: {str(e)}")
                raise UserError(_("Error de conexión al pausar la publicación: %s") % str(e))

    def action_activate(self):
        for rec in self:
            if rec.status != 'paused' or not rec.meli_id:
                continue
            
            rec.instance_id.check_token_validity()
            url = f"https://api.mercadolibre.com/items/{rec.meli_id}"
            try:
                response = rec.instance_id._call_api('PUT', url, json={'status': 'active'})
                if response.status_code == 200:
                    rec.status = 'active'
                    rec.message_post(body="Publicación activada en MercadoLibre.")
                    _logger.info(f"Successfully activated item {rec.meli_id}")
                else:
                    _logger.error(f"Error activating item {rec.meli_id}: {response.text}")
                    raise UserError(_("Error activating item: %s") % response.text)
            except UserError:
                raise
            except Exception as e:
                _logger.error(f"Exception while activating item {rec.meli_id}: {str(e)}")
                raise UserError(_("Error de conexión al activar la publicación: %s") % str(e))

    def action_close(self):
        for rec in self:
            if not rec.meli_id:
                rec.status = 'closed'
                continue
            
            rec.instance_id.check_token_validity()
            url = f"https://api.mercadolibre.com/items/{rec.meli_id}"
            try:
                response = rec.instance_id._call_api('PUT', url, json={'status': 'closed'})
                if response.status_code == 200:
                    rec.status = 'closed'
                    rec.message_post(body="Publicación cerrada en MercadoLibre.")
                    _logger.info(f"Successfully closed item {rec.meli_id}")
                else:
                    _logger.error(f"Error closing item {rec.meli_id}: {response.text}")
                    raise UserError(_("Error closing item: %s") % response.text)
            except UserError:
                raise
            except Exception as e:
                _logger.error(f"Exception while closing item {rec.meli_id}: {str(e)}")
                raise UserError(_("Error de conexión al cerrar la publicación: %s") % str(e))

    def unlink(self):
        for rec in self:
            if rec.status not in ['draft', 'closed', 'error']:
                raise UserError(_("No puedes eliminar una publicación activa o pausada en MercadoLibre. Cérrala primero."))
        return super().unlink()

    def action_check_status(self):
        """Fetch current item status from ML and update Odoo record."""
        for rec in self:
            if not rec.meli_id:
                continue
            
            url = f"https://api.mercadolibre.com/items/{rec.meli_id}"
            response = rec.instance_id._call_api('GET', url)
            
            if response.status_code == 200:
                data = response.json()
                status_map = {
                    'active': 'active',
                    'paused': 'paused',
                    'closed': 'closed',
                    'under_review': 'paused',
                    'inactive': 'paused',
                }
                new_status = status_map.get(data.get('status'), 'error')
                vals = {
                    'status': new_status,
                    'price': data.get('price'),
                    'available_quantity': data.get('available_quantity'),
                    'permalink': data.get('permalink'),
                }
                vals.update(self._extract_convivencia_vals(data))
                rec.write(vals)
                rec.message_post(body=f"Estado verificado en ML: {data.get('status')} (Odoo: {new_status})")
            else:
                rec.message_post(body=f"Error verificando estado en ML: {response.text}")

    def _sync_selling_address_stock(self, stock):
        """
        Update the seller-warehouse stock (selling_address) for a FULL+Flex item
        via the user-products endpoint. The meli_facility (FULL) stock is managed
        by ML and is never touched from Odoo.

        Requires the x-version header obtained from a previous GET; on 409
        (version conflict) the GET+PUT cycle is retried once.
        Rate limit of this resource: 100 RPM.
        """
        self.ensure_one()
        base = f"https://api.mercadolibre.com/user-products/{self.user_product_id}/stock"

        for attempt in (1, 2):
            get_resp = self.instance_id._call_api('GET', base)
            if get_resp.status_code != 200:
                _logger.error(
                    "ML user-product %s: error fetching stock (%s): %s",
                    self.user_product_id, get_resp.status_code, get_resp.text,
                )
                return False

            x_version = get_resp.headers.get('x-version')
            if not x_version:
                _logger.error("ML user-product %s: no x-version header in response", self.user_product_id)
                return False

            current = next(
                (l.get('quantity') for l in get_resp.json().get('locations', [])
                 if l.get('type') == 'selling_address'),
                None,
            )
            if current == stock:
                return True  # already in sync

            put_resp = self.instance_id._call_api(
                'PUT', f"{base}/type/selling_address",
                json={'quantity': stock},
                headers={'x-version': str(x_version)},
            )
            if put_resp.status_code in (200, 204):
                _logger.info(
                    "ML user-product %s: selling_address stock set to %s", self.user_product_id, stock,
                )
                return True
            if put_resp.status_code == 409 and attempt == 1:
                _logger.info("ML user-product %s: x-version conflict, retrying", self.user_product_id)
                continue

            _logger.error(
                "ML user-product %s: stock update failed (%s): %s",
                self.user_product_id, put_resp.status_code, put_resp.text,
            )
            return False
        return False

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

            price_changed = price != rec.price
            stock_changed = stock != rec.available_quantity
            if not price_changed and not stock_changed:
                continue

            rec.price = price
            rec.available_quantity = stock

            try:
                if rec.is_full_flex and rec.user_product_id:
                    # Convivencia FULL+Flex: stock must go through user-products
                    # (PUT /items with available_quantity is rejected for these items)
                    if stock_changed:
                        rec._sync_selling_address_stock(stock)
                    if price_changed:
                        url = f"https://api.mercadolibre.com/items/{rec.meli_id}"
                        response = rec.instance_id._call_api('PUT', url, json={'price': price})
                        if response.status_code != 200:
                            _logger.error(f"Failed to sync price for {rec.meli_id}: {response.text}")
                else:
                    url = f"https://api.mercadolibre.com/items/{rec.meli_id}"
                    data = {'price': price, 'available_quantity': stock}
                    response = rec.instance_id._call_api('PUT', url, json=data)
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
            user_response = instance._call_api('GET', "https://api.mercadolibre.com/users/me")
            if user_response.status_code == 200:
                instance.seller_id = str(user_response.json().get('id'))
            else:
                raise UserError(_("Could not fetch seller ID for item import."))

        url = f"https://api.mercadolibre.com/users/{instance.seller_id}/items/search"
        
        # Paginate through results
        offset = 0
        limit = 50
        while True:
            params = {'offset': offset, 'limit': limit}
            response = instance._call_api('GET', url, params=params)
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
                det_resp = instance._call_api('GET', details_url)
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
    def _extract_convivencia_vals(self, data):
        """
        Extract user_product_id and FULL+Flex convivencia flag from an ML item payload.
        Convivencia = logistic_type 'fulfillment' + tag 'self_service_in' on shipping.
        For items with variations, user_product_id lives inside the variations array.
        """
        shipping = data.get('shipping') or {}
        ship_tags = shipping.get('tags') or []
        is_full_flex = (
            shipping.get('logistic_type') == 'fulfillment'
            and 'self_service_in' in ship_tags
        )

        user_product_id = data.get('user_product_id')
        if not user_product_id:
            variations = data.get('variations') or []
            if variations:
                user_product_id = variations[0].get('user_product_id')

        return {
            'user_product_id': user_product_id or False,
            'is_full_flex': is_full_flex,
        }

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
        vals.update(self._extract_convivencia_vals(data))
        
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
                # Create a minimal product template that tracks stock (Odoo 18/19)
                product = self.env['product.template'].create({
                    'name': data.get('title'),
                    'list_price': data.get('price'),
                    'default_code': sku or '',
                    'type': 'consu',
                    'is_storable': True,
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
        items.with_context(cron_mode=True).action_sync_price_stock()

