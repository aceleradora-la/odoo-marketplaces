import hashlib
import hmac
import json
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.marketplace_base.models.push_config import (
    acelio_dump,
    acelio_post,
    acelio_sign,
)

SECRET = 'un-secreto-de-prueba'


class _FakeResponse:
    def __init__(self, status_code=200, text='{"ok":true}'):
        self.status_code = status_code
        self.text = text


@tagged('post_install', '-at_install')
class TestAcelioSignature(TransactionCase):
    """La firma es HMAC-SHA256 hex del cuerpo EXACTO que se envía."""

    def test_body_is_canonical_json(self):
        body = acelio_dump({'b': 2, 'a': 1})
        self.assertEqual(body, '{"a":1,"b":2}')
        self.assertEqual(json.loads(body)['a'], 1)

    def test_signature_matches_manual_hmac(self):
        body = acelio_dump({'tenant': 'demo', 'picking_id': 5})
        expected = hmac.new(
            SECRET.encode('utf-8'), body.encode('utf-8'), hashlib.sha256
        ).hexdigest()
        self.assertEqual(acelio_sign(SECRET, body), expected)

    def test_post_sends_signed_body(self):
        body = acelio_dump({'tenant': 'demo'})
        with patch('odoo.addons.marketplace_base.models.push_config.requests') as req:
            req.post.return_value = _FakeResponse()
            ok, _message = acelio_post('https://acelio.test/hook', SECRET, body)
        self.assertTrue(ok)
        _args, kwargs = req.post.call_args
        self.assertEqual(kwargs['data'], body.encode('utf-8'))
        self.assertEqual(kwargs['timeout'], 5)
        self.assertEqual(kwargs['headers']['X-Acelio-Signature'], acelio_sign(SECRET, body))

    def test_post_never_raises_on_network_error(self):
        with patch('odoo.addons.marketplace_base.models.push_config.requests') as req:
            req.post.side_effect = Exception('connection refused')
            ok, message = acelio_post('https://acelio.test/hook', SECRET, '{}')
        self.assertFalse(ok)
        self.assertIn('connection refused', message)

    def test_post_reports_http_error(self):
        with patch('odoo.addons.marketplace_base.models.push_config.requests') as req:
            req.post.return_value = _FakeResponse(status_code=401, text='firma inválida')
            ok, message = acelio_post('https://acelio.test/hook', SECRET, '{}')
        self.assertFalse(ok)
        self.assertIn('401', message)


@tagged('post_install', '-at_install')
class TestAcelioPushConfig(TransactionCase):

    def _config(self, **overrides):
        vals = {
            'acelio_webhook_url': 'https://acelio.test/api/sync/webhooks/odoo',
            'acelio_tenant': 'demo',
            'acelio_secret': SECRET,
        }
        vals.update(overrides)
        return self.env['marketplace.push.config'].create(vals)

    def test_get_active_ignores_incomplete_or_disabled(self):
        Config = self.env['marketplace.push.config']
        self.assertFalse(Config._get_active())
        config = self._config(enabled=False)
        self.assertFalse(Config._get_active())
        config.enabled = True
        self.assertEqual(Config._get_active(), config)
        config.acelio_secret = False
        self.assertFalse(Config._get_active())

    def test_only_one_config_per_company(self):
        self._config()
        with self.assertRaises(UserError):
            self._config(acelio_tenant='otro')

    def test_test_connection_requires_full_config(self):
        config = self._config(acelio_secret=False)
        with self.assertRaises(UserError):
            config.action_test_connection()

    def test_test_connection_logs_and_raises_on_failure(self):
        config = self._config()
        with patch('odoo.addons.marketplace_base.models.push_config.requests') as req:
            req.post.side_effect = Exception('timeout')
            with self.assertRaises(UserError):
                config.action_test_connection()
        self.assertEqual(config.last_push_state, 'error')
        log = self.env['marketplace.sync.log'].search(
            [('channel', '=', 'other'), ('reference', '=', 'ping')], limit=1)
        self.assertTrue(log)
        self.assertEqual(log.state, 'error')

    def test_test_connection_success(self):
        config = self._config()
        with patch('odoo.addons.marketplace_base.models.push_config.requests') as req:
            req.post.return_value = _FakeResponse()
            action = config.action_test_connection()
        self.assertEqual(action['tag'], 'display_notification')
        self.assertEqual(config.last_push_state, 'success')


@tagged('post_install', '-at_install')
class TestAcelioPickingPush(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env['product.product'].create({
            'name': 'Producto Push Test',
            'is_storable': True,
        })
        cls.partner = cls.env['res.partner'].create({'name': 'Cliente Push Test'})

    def _validated_picking(self, client_order_ref):
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'client_order_ref': client_order_ref,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'product_uom_qty': 1.0,
                'price_unit': 100.0,
            })],
        })
        order.action_confirm()
        picking = order.picking_ids[:1]
        self.assertTrue(picking, "El pedido debería generar una entrega")
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        picking._action_done()
        self.assertEqual(picking.state, 'done')
        return order, picking

    def test_channel_detected_from_client_order_ref(self):
        _order, tn_picking = self._validated_picking('TN 1234567')
        self.assertEqual(tn_picking._marketplace_channel(), 'tiendanube')
        _order, ml_picking = self._validated_picking('ML 2000000001')
        self.assertEqual(ml_picking._marketplace_channel(), 'meli')

    def test_no_push_for_non_marketplace_picking(self):
        self.env['marketplace.push.config'].create({
            'acelio_webhook_url': 'https://acelio.test/api/sync/webhooks/odoo',
            'acelio_tenant': 'demo',
            'acelio_secret': SECRET,
        })
        _order, picking = self._validated_picking('PED-INTERNO-1')
        self.assertFalse(picking._marketplace_channel())
        self.assertFalse(picking._marketplace_acelio_payload('demo'))
        self.assertEqual(picking._marketplace_notify_acelio(), 0)

    def test_payload_shape(self):
        order, picking = self._validated_picking('TN 1234567')
        payload = picking._marketplace_acelio_payload('demo')
        self.assertEqual(sorted(payload.keys()), [
            'client_order_ref', 'picking_id', 'picking_name', 'sale_order_id',
            'tenant', 'tracking_ref', 'validated_at',
        ])
        self.assertEqual(payload['tenant'], 'demo')
        self.assertEqual(payload['client_order_ref'], 'TN 1234567')
        self.assertEqual(payload['picking_id'], picking.id)
        self.assertEqual(payload['sale_order_id'], order.id)
        self.assertTrue(payload['validated_at'].endswith('Z'))
        # El payload viaja tal cual se firma
        acelio_sign(SECRET, acelio_dump(payload))

    def test_push_is_scheduled_after_commit_not_inline(self):
        self.env['marketplace.push.config'].create({
            'acelio_webhook_url': 'https://acelio.test/api/sync/webhooks/odoo',
            'acelio_tenant': 'demo',
            'acelio_secret': SECRET,
        })
        with patch('odoo.addons.marketplace_base.models.push_config.requests') as req:
            _order, picking = self._validated_picking('TN 7654321')
            # Se encola para después del commit: nada de red durante la validación.
            req.post.assert_not_called()
        self.assertEqual(picking.state, 'done')

    def test_validation_survives_a_broken_notification(self):
        """El aviso jamás puede voltear la validación de la entrega."""
        Picking = type(self.env['stock.picking'])
        with patch.object(Picking, '_marketplace_notify_acelio',
                          side_effect=Exception('boom')):
            _order, picking = self._validated_picking('TN 999')
        self.assertEqual(picking.state, 'done')
