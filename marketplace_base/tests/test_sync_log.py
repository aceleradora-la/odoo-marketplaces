import json

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


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
