from odoo import models, fields, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class TnVariant(models.Model):
    """
    Maps a TiendaNube product variant to an Odoo product.product.
    Created automatically when a product is published or synced.
    """
    _name = 'tn.variant'
    _description = 'TiendaNube Variant'

    tn_variant_id = fields.Char('TN Variant ID', readonly=True, index=True)
    tn_product_id = fields.Many2one('tn.product', 'TN Product', ondelete='cascade', required=True)
    instance_id = fields.Many2one('tn.instance', 'Store', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', 'Odoo Variant')

    price = fields.Float('Price in TN')
    stock = fields.Integer('Stock in TN')
    sku = fields.Char('SKU')

    def action_sync_price_stock(self):
        for rec in self:
            if not rec.tn_variant_id or not rec.tn_product_id.tn_product_id:
                continue

            price = rec.price
            stock = rec.stock

            if rec.product_id:
                if rec.instance_id.pricelist_id:
                    price = rec.instance_id.pricelist_id._get_product_price(rec.product_id, 1, False)
                if rec.instance_id.stock_location_ids:
                    quants = rec.env['stock.quant'].search([
                        ('product_id', '=', rec.product_id.id),
                        ('location_id', 'child_of', rec.instance_id.stock_location_ids.ids),
                    ])
                    stock = max(0, int(
                        sum(quants.mapped('quantity')) - sum(quants.mapped('reserved_quantity'))
                    ))

            if price == rec.price and stock == rec.stock:
                continue

            tn_product_id = rec.tn_product_id.tn_product_id
            resp = rec.instance_id._call_api(
                'PUT',
                f'/products/{tn_product_id}/variants/{rec.tn_variant_id}',
                json={'price': str(price), 'stock': stock},
            )
            if resp.status_code == 200:
                rec.write({'price': price, 'stock': stock})
                _logger.info("TN variant %s synced — price=%s stock=%s", rec.tn_variant_id, price, stock)
            else:
                _logger.error("TN variant sync error %s: %s", rec.tn_variant_id, resp.text)
