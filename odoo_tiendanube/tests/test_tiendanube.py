from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTiendaNubeOrders(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.instance = cls.env['tn.instance'].create({
            'name': 'Test TN Store',
            'app_id': 'tn_app',
            'app_secret': 'tn_secret',
            'contact_email': 'dev@test.com',
            'state': 'authenticated',
            'access_token': 'tn_token',
            'tn_store_id': '5555',
        })
        cls.product = cls.env['product.template'].create({
            'name': 'Producto Test TN',
            'type': 'consu',
            'is_storable': True,
            'list_price': 50.0,
        })
        cls.tn_product = cls.env['tn.product'].create({
            'name': 'Producto Test TN',
            'instance_id': cls.instance.id,
            'product_id': cls.product.id,
            'tn_product_id': '9001',
            'status': 'active',
        })
        cls.tn_variant = cls.env['tn.variant'].create({
            'tn_variant_id': '8001',
            'tn_product_id': cls.tn_product.id,
            'instance_id': cls.instance.id,
            'product_id': cls.product.product_variant_id.id,
            'price': 50.0,
            'stock': 10,
        })
        cls.journal_bank = cls.env['account.journal'].search(
            [('type', '=', 'bank'), ('company_id', '=', cls.env.company.id)], limit=1,
        ) or cls.env['account.journal'].create({
            'name': 'Test Bank TN', 'type': 'bank', 'code': 'TNBK',
        })

    def _order_payload(self, **overrides):
        payload = {
            'id': 70001,
            'payment_status': 'paid',
            'gateway': 'mercadopago',
            'payment_details': {'method': 'credit_card'},
            'total': '100.00',
            'contact_email': 'cliente@test.com',
            'contact_name': 'Cliente TN',
            'contact_phone': '1199887766',
            'customer': {'id': 4321},
            'shipping_address': {
                'address': 'Calle Falsa 123', 'city': 'CABA',
                'zipcode': '1414', 'country': 'AR',
            },
            'products': [
                {'product_id': 9001, 'variant_id': 8001, 'quantity': 2, 'price': '50.00'},
            ],
        }
        payload.update(overrides)
        return payload

    # ------------------------------------------------------------------
    # Payment journal resolution
    # ------------------------------------------------------------------

    def test_resolve_journal_gateway_and_method(self):
        mapped = self.env['account.journal'].create({
            'name': 'MP Credit', 'type': 'bank', 'code': 'MPCR',
        })
        self.env['tn.payment.method'].create({
            'instance_id': self.instance.id,
            'gateway_name': 'mercadopago',
            'payment_method_name': 'credit_card',
            'journal_id': mapped.id,
        })
        journal = self.instance._resolve_payment_journal(self._order_payload())
        self.assertEqual(journal, mapped)

    def test_resolve_journal_gateway_only(self):
        mapped = self.env['account.journal'].create({
            'name': 'MP Generic', 'type': 'bank', 'code': 'MPGN',
        })
        self.env['tn.payment.method'].create({
            'instance_id': self.instance.id,
            'gateway_name': 'mercadopago',
            'journal_id': mapped.id,
        })
        journal = self.instance._resolve_payment_journal(
            self._order_payload(payment_details={'method': 'debit_card'})
        )
        self.assertEqual(journal, mapped)

    def test_resolve_journal_fallback(self):
        journal = self.instance._resolve_payment_journal(
            self._order_payload(gateway='gateway_desconocido')
        )
        self.assertTrue(journal)
        self.assertIn(journal.type, ('bank', 'cash'))

    # ------------------------------------------------------------------
    # Partner enrichment
    # ------------------------------------------------------------------

    def test_partner_created_with_address(self):
        partner = self.instance._get_or_create_partner(self._order_payload())
        self.assertEqual(partner.email, 'cliente@test.com')
        self.assertEqual(partner.street, 'Calle Falsa 123')
        self.assertEqual(partner.city, 'CABA')
        self.assertEqual(partner.zip, '1414')
        self.assertEqual(partner.country_id.code, 'AR')
        self.assertEqual(partner.tn_customer_id, '4321')

    def test_partner_matched_by_email(self):
        existing = self.env['res.partner'].create({
            'name': 'Cliente Previo', 'email': 'cliente@test.com',
        })
        partner = self.instance._get_or_create_partner(self._order_payload())
        self.assertEqual(partner, existing)

    # ------------------------------------------------------------------
    # Order processing
    # ------------------------------------------------------------------

    def test_process_order_creates_confirmed_so(self):
        with patch.object(type(self.instance), '_register_payment_on_order'):
            self.instance._process_single_order(self._order_payload())
        so = self.env['sale.order'].search([('tn_order_id', '=', '70001')])
        self.assertEqual(len(so), 1)
        self.assertEqual(so.state, 'sale')
        self.assertEqual(so.order_line.product_uom_qty, 2)
        self.assertEqual(so.tn_gateway, 'mercadopago')
        self.assertEqual(so.tn_payment_method, 'credit_card')
        self.assertEqual(so.company_id, self.instance.company_id)

    def test_process_order_matches_by_variant(self):
        with patch.object(type(self.instance), '_register_payment_on_order'):
            self.instance._process_single_order(self._order_payload())
        so = self.env['sale.order'].search([('tn_order_id', '=', '70001')])
        self.assertEqual(so.order_line.product_id, self.product.product_variant_id)

    def test_process_order_skips_duplicate(self):
        with patch.object(type(self.instance), '_register_payment_on_order'):
            self.instance._process_single_order(self._order_payload())
            self.instance._process_single_order(self._order_payload())
        so = self.env['sale.order'].search([('tn_order_id', '=', '70001')])
        self.assertEqual(len(so), 1)

    def test_process_order_skips_unpaid(self):
        self.instance._process_single_order(
            self._order_payload(id=70002, payment_status='pending')
        )
        self.assertFalse(self.env['sale.order'].search([('tn_order_id', '=', '70002')]))

    def test_process_order_no_matching_product(self):
        self.instance._process_single_order(self._order_payload(
            id=70003,
            products=[{'product_id': 404, 'variant_id': 404, 'quantity': 1, 'price': '10.00'}],
        ))
        self.assertFalse(self.env['sale.order'].search([('tn_order_id', '=', '70003')]))
