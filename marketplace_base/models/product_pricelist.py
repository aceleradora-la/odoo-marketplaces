from odoo import models


class ProductPricelist(models.Model):
    _inherit = 'product.pricelist'

    def get_marketplace_prices(self, product_ids):
        """
        Public RPC helper for external sync engines (Acelio Sync).

        The native pricelist engine (`_get_products_price`) is private and
        therefore blocked by /web/dataset/call_kw. This thin public wrapper
        exposes the exact computed price per product — covering every rule
        type: fixed, percentage, formula, chained pricelists, date ranges.

        :param product_ids: list of product.product ids
        :return: dict {str(product_id): float price} (str keys for JSON safety)
        """
        self.ensure_one()
        products = self.env['product.product'].browse(product_ids).exists()
        if not products:
            return {}
        prices = self._get_products_price(products, 1.0)
        return {str(pid): price for pid, price in prices.items()}
