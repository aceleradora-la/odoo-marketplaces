import json

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestMarketplacePrices(TransactionCase):

    def test_helper_resolves_chained_percentage_pricelist(self):
        """The RPC helper must return engine-computed prices, including
        formula rules based on another pricelist (the case that a naive
        fixed-rule reader cannot resolve)."""
        product = self.env['product.product'].create({
            'name': 'Producto Precio Test', 'list_price': 1000.0,
        })
        base_list = self.env['product.pricelist'].create({
            'name': 'Base Test',
            'item_ids': [(0, 0, {
                'applied_on': '3_global',
                'compute_price': 'fixed',
                'fixed_price': 800.0,
            })],
        })
        chained = self.env['product.pricelist'].create({
            'name': 'Mayorista Test',
            'item_ids': [(0, 0, {
                'applied_on': '3_global',
                'compute_price': 'formula',
                'base': 'pricelist',
                'base_pricelist_id': base_list.id,
                'price_discount': 10.0,   # base -10%
            })],
        })
        prices = chained.get_marketplace_prices([product.id])
        self.assertEqual(prices[str(product.id)], 720.0)  # 800 - 10%

    def test_helper_empty_and_missing_products(self):
        pricelist = self.env['product.pricelist'].create({'name': 'Vacia Test'})
        self.assertEqual(pricelist.get_marketplace_prices([]), {})
        self.assertEqual(pricelist.get_marketplace_prices([99999999]), {})


@tagged('post_install', '-at_install')
class TestMarketplaceSyncLog(TransactionCase):

    def test_log_event_creates_record(self):
        log = self.env['marketplace.sync.log'].log_event(
            'meli', 'order_webhook', 'error',
            instance_name='Cuenta Test', reference='123',
            message='Algo falló', payload={'id': 123, 'status': 'paid'},
        )
        self.assertTrue(log)
        self.assertEqual(log.channel, 'meli')
        self.assertEqual(log.state, 'error')
        self.assertEqual(json.loads(log.payload)['id'], 123)

    def test_log_event_never_raises(self):
        # Invalid channel would violate the selection — log_event must swallow it
        log = self.env['marketplace.sync.log'].log_event(
            'invalid_channel', 'other', 'error',
        )
        self.assertFalse(log)

    def test_retry_requires_order_operation(self):
        log = self.env['marketplace.sync.log'].log_event(
            'meli', 'stock_sync', 'error', message='x',
        )
        with self.assertRaises(UserError):
            log.action_retry()

    def test_retry_requires_payload(self):
        log = self.env['marketplace.sync.log'].log_event(
            'meli', 'order_webhook', 'error', message='sin payload',
        )
        with self.assertRaises(UserError):
            log.action_retry()

    def test_cleanup_removes_old_success(self):
        log = self.env['marketplace.sync.log'].log_event(
            'tiendanube', 'order_sync', 'success', message='viejo',
        )
        # Backdate beyond the 30-day window
        self.env.cr.execute(
            "UPDATE marketplace_sync_log SET create_date = now() - interval '60 days' "
            "WHERE id = %s", (log.id,),
        )
        log.invalidate_recordset()
        self.env['marketplace.sync.log'].cron_cleanup()
        self.assertFalse(log.exists())
