from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class TnProduct(models.Model):
    """
    Represents a TiendaNube product linked to an Odoo product.template.
    One tn.product per product.template per store instance.
    Variants are managed in tn.variant.
    """
    _name = 'tn.product'
    _description = 'TiendaNube Product'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char('Product Name', required=True, tracking=True)
    instance_id = fields.Many2one('tn.instance', 'Store', required=True, ondelete='cascade', tracking=True)
    product_id = fields.Many2one('product.template', 'Odoo Product', required=True, tracking=True)

    tn_product_id = fields.Char('TiendaNube Product ID', readonly=True, index=True)
    tn_url = fields.Char('URL en Tienda', readonly=True)

    status = fields.Selection([
        ('draft', 'No publicado'),
        ('active', 'Publicado'),
        ('error', 'Error'),
    ], default='draft', readonly=True, tracking=True)

    variant_ids = fields.One2many('tn.variant', 'tn_product_id', string='Variants')
    variant_count = fields.Integer(compute='_compute_variant_count')

    @api.depends('variant_ids')
    def _compute_variant_count(self):
        for rec in self:
            rec.variant_count = len(rec.variant_ids)

    # -------------------------------------------------------------------------
    # Publish
    # -------------------------------------------------------------------------

    def _build_product_payload(self):
        """Build the JSON payload to create/update the product in TiendaNube."""
        self.ensure_one()
        tmpl = self.product_id

        # Price: use instance pricelist or product list_price
        price = tmpl.list_price
        if self.instance_id.pricelist_id:
            price = self.instance_id.pricelist_id._get_product_price(tmpl, 1, False)

        # Stock per variant or product
        stock = self._get_stock()

        # Images
        images = []
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        if tmpl.image_1920:
            images.append({'src': f"{base_url}/tn_image/product.template/{tmpl.id}/image_1920"})

        # Variants from Odoo product variants
        variants = []
        for variant in tmpl.product_variant_ids:
            v_price = price
            if self.instance_id.pricelist_id:
                v_price = self.instance_id.pricelist_id._get_product_price(variant, 1, False)
            v_stock = self._get_variant_stock(variant)

            values = [{'es': val.name} for val in variant.product_template_attribute_value_ids.mapped('product_attribute_value_id')]
            variants.append({
                'price': str(v_price),
                'stock': v_stock,
                'sku': variant.default_code or '',
                'values': values or [{'es': self.name}],
            })

        if not variants:
            variants = [{'price': str(price), 'stock': stock, 'sku': tmpl.default_code or ''}]

        return {
            'name': {'es': self.name},
            'description': {'es': tmpl.description_sale or ''},
            'published': True,
            'variants': variants,
            'images': images,
        }

    def _get_stock(self):
        tmpl = self.product_id
        if self.instance_id.stock_location_ids:
            quants = self.env['stock.quant'].search([
                ('product_id', 'in', tmpl.product_variant_ids.ids),
                ('location_id', 'child_of', self.instance_id.stock_location_ids.ids),
            ])
            return max(0, int(sum(quants.mapped('quantity')) - sum(quants.mapped('reserved_quantity'))))
        return int(tmpl.qty_available)

    def _get_variant_stock(self, variant):
        if self.instance_id.stock_location_ids:
            quants = self.env['stock.quant'].search([
                ('product_id', '=', variant.id),
                ('location_id', 'child_of', self.instance_id.stock_location_ids.ids),
            ])
            return max(0, int(sum(quants.mapped('quantity')) - sum(quants.mapped('reserved_quantity'))))
        return int(variant.qty_available)

    def action_publish(self):
        for rec in self:
            if rec.status == 'active':
                continue
            payload = rec._build_product_payload()
            resp = rec.instance_id._call_api('POST', '/products', json=payload)
            if resp.status_code in (200, 201):
                data = resp.json()
                tn_id = str(data.get('id'))
                rec.write({
                    'tn_product_id': tn_id,
                    'tn_url': data.get('canonical_url', ''),
                    'status': 'active',
                })
                rec._sync_variants_from_response(data.get('variants', []))
                rec.message_post(body=f"Publicado en TiendaNube. ID: {tn_id}")
                _logger.info("TN product published: %s → %s", rec.name, tn_id)
            else:
                rec.write({'status': 'error'})
                rec.message_post(body=f"Error al publicar: {resp.text}")
                _logger.error("TN publish error for %s: %s", rec.name, resp.text)

    def action_update(self):
        """Push price, stock, name and description changes to TiendaNube."""
        for rec in self:
            if not rec.tn_product_id:
                continue
            payload = rec._build_product_payload()
            # Remove variants from update payload — variants are synced separately
            payload.pop('variants', None)
            resp = rec.instance_id._call_api('PUT', f'/products/{rec.tn_product_id}', json=payload)
            if resp.status_code == 200:
                rec.message_post(body="Producto actualizado en TiendaNube.")
            else:
                _logger.error("TN update error for %s: %s", rec.name, resp.text)

    def action_delete(self):
        for rec in self:
            if not rec.tn_product_id:
                rec.write({'status': 'draft'})
                continue
            resp = rec.instance_id._call_api('DELETE', f'/products/{rec.tn_product_id}')
            if resp.status_code in (200, 204):
                rec.write({'tn_product_id': False, 'status': 'draft'})
                rec.message_post(body="Producto eliminado de TiendaNube.")
            else:
                _logger.error("TN delete error for %s: %s", rec.name, resp.text)

    def action_sync_price_stock(self):
        """Sync price and stock for all variants of this product."""
        for rec in self:
            if not rec.tn_product_id:
                continue
            for variant_rec in rec.variant_ids:
                variant_rec.action_sync_price_stock()

    def _sync_variants_from_response(self, tn_variants):
        """Create/update tn.variant records from the TN API response."""
        for v in tn_variants:
            tn_var_id = str(v.get('id'))
            sku = v.get('sku', '')
            odoo_variant = False
            if sku:
                odoo_variant = self.env['product.product'].search([('default_code', '=', sku)], limit=1)
            if not odoo_variant:
                odoo_variant = self.product_id.product_variant_id

            existing = self.env['tn.variant'].search([
                ('tn_variant_id', '=', tn_var_id),
                ('instance_id', '=', self.instance_id.id),
            ], limit=1)
            vals = {
                'tn_variant_id': tn_var_id,
                'tn_product_id': self.id,
                'instance_id': self.instance_id.id,
                'product_id': odoo_variant.id if odoo_variant else False,
                'price': float(v.get('price') or 0),
                'stock': int(v.get('stock') or 0),
                'sku': sku,
            }
            if existing:
                existing.write(vals)
            else:
                self.env['tn.variant'].create(vals)

    # -------------------------------------------------------------------------
    # Cron: sync price/stock to TN
    # -------------------------------------------------------------------------

    @api.model
    def cron_sync_price_stock(self):
        products = self.search([('status', '=', 'active')])
        products.with_context(cron_mode=True).action_sync_price_stock()
