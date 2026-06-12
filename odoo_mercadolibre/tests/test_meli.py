from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestMeliOrders(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.instance = cls.env['meli.instance'].create({
            'name': 'Test ML Account',
            'site_id': 'MLA',
            'app_id': 'test_app',
            'secret_key': 'test_secret',
            'redirect_uri': 'https://test/meli/auth',
            'state': 'authenticated',
            'access_token': 'test_token',
            'seller_id': '111',
        })
        cls.product = cls.env['product.template'].create({
            'name': 'Producto Test ML',
            'type': 'consu',
            'is_storable': True,
            'list_price': 100.0,
        })
        cls.item = cls.env['meli.item'].create({
            'name': 'Publicación Test',
            'product_id': cls.product.id,
            'instance_id': cls.instance.id,
            'price': 100.0,
            'available_quantity': 5,
            'meli_id': 'MLA111',
            'status': 'active',
        })
        cls.journal_bank = cls.env['account.journal'].search(
            [('type', '=', 'bank'), ('company_id', '=', cls.env.company.id)], limit=1,
        ) or cls.env['account.journal'].create({
            'name': 'Test Bank', 'type': 'bank', 'code': 'TBNK',
        })

    def _order_payload(self, **overrides):
        payload = {
            'id': 12345,
            'status': 'paid',
            'tags': [],
            'shipping': {'logistic_type': 'me2'},
            'buyer': {'id': 999, 'nickname': 'COMPRADOR_TEST', 'email': 'buyer@test.com'},
            'order_items': [
                {'item': {'id': 'MLA111'}, 'quantity': 2, 'unit_price': 100.0},
            ],
            'payments': [
                {'status': 'approved', 'payment_method_id': 'visa',
                 'payment_type_id': 'credit_card', 'total_paid_amount': 200.0},
            ],
        }
        payload.update(overrides)
        return payload

    # ------------------------------------------------------------------
    # FULL detection
    # ------------------------------------------------------------------

    def test_is_full_order_by_tag(self):
        order = self._order_payload(tags=['fulfillment'])
        self.assertTrue(self.instance._is_full_order(order))

    def test_is_full_order_by_logistic_type(self):
        order = self._order_payload(shipping={'logistic_type': 'fulfillment'})
        self.assertTrue(self.instance._is_full_order(order))

    def test_is_not_full_order(self):
        self.assertFalse(self.instance._is_full_order(self._order_payload()))

    # ------------------------------------------------------------------
    # Convivencia FULL + Flex detection
    # ------------------------------------------------------------------

    def test_extract_convivencia_full_flex(self):
        data = {
            'user_product_id': 'MLAU123',
            'shipping': {'logistic_type': 'fulfillment', 'tags': ['self_service_in']},
        }
        vals = self.env['meli.item']._extract_convivencia_vals(data)
        self.assertTrue(vals['is_full_flex'])
        self.assertEqual(vals['user_product_id'], 'MLAU123')

    def test_extract_convivencia_not_flex(self):
        data = {'shipping': {'logistic_type': 'fulfillment', 'tags': []}}
        vals = self.env['meli.item']._extract_convivencia_vals(data)
        self.assertFalse(vals['is_full_flex'])

    def test_extract_convivencia_user_product_from_variation(self):
        data = {
            'shipping': {},
            'variations': [{'user_product_id': 'MLAU456'}],
        }
        vals = self.env['meli.item']._extract_convivencia_vals(data)
        self.assertEqual(vals['user_product_id'], 'MLAU456')

    # ------------------------------------------------------------------
    # Payment journal resolution
    # ------------------------------------------------------------------

    def test_resolve_journal_by_method(self):
        mapped = self.env['account.journal'].create({
            'name': 'Visa Journal', 'type': 'bank', 'code': 'VISA',
        })
        self.env['meli.payment.method'].create({
            'instance_id': self.instance.id,
            'payment_method_id': 'visa',
            'journal_id': mapped.id,
        })
        journal = self.instance._resolve_payment_journal(
            [{'status': 'approved', 'payment_method_id': 'visa', 'payment_type_id': 'credit_card'}],
            self.env.company,
        )
        self.assertEqual(journal, mapped)

    def test_resolve_journal_by_type_fallback(self):
        mapped = self.env['account.journal'].create({
            'name': 'Cards Journal', 'type': 'bank', 'code': 'CARD',
        })
        self.env['meli.payment.method'].create({
            'instance_id': self.instance.id,
            'payment_type_id': 'credit_card',
            'journal_id': mapped.id,
        })
        journal = self.instance._resolve_payment_journal(
            [{'status': 'approved', 'payment_method_id': 'amex', 'payment_type_id': 'credit_card'}],
            self.env.company,
        )
        self.assertEqual(journal, mapped)

    def test_resolve_journal_default_fallback(self):
        journal = self.instance._resolve_payment_journal(
            [{'status': 'approved', 'payment_method_id': 'unknown_method'}],
            self.env.company,
        )
        self.assertTrue(journal)
        self.assertIn(journal.type, ('bank', 'cash'))

    # ------------------------------------------------------------------
    # Partner enrichment
    # ------------------------------------------------------------------

    def test_partner_created_with_buyer_data(self):
        partner = self.instance._get_or_create_partner({
            'id': 777, 'nickname': 'NUEVO_COMPRADOR', 'email': 'nuevo@test.com',
            'phone': {'number': '1122334455'},
        })
        self.assertEqual(partner.meli_user_id, '777')
        self.assertEqual(partner.email, 'nuevo@test.com')
        self.assertEqual(partner.phone, '1122334455')

    def test_partner_existing_data_not_overwritten(self):
        existing = self.env['res.partner'].create({
            'name': 'Cliente Existente',
            'email': 'original@test.com',
            'meli_user_id': '888',
        })
        partner = self.instance._get_or_create_partner({
            'id': 888, 'nickname': 'NICK', 'email': 'otro@test.com',
        })
        self.assertEqual(partner, existing)
        self.assertEqual(partner.email, 'original@test.com')

    # ------------------------------------------------------------------
    # Order processing
    # ------------------------------------------------------------------

    def test_process_order_creates_confirmed_so(self):
        with patch.object(type(self.instance), '_register_payment_on_order'):
            self.instance._process_single_order(self._order_payload())
        so = self.env['sale.order'].search([('meli_order_id', '=', '12345')])
        self.assertEqual(len(so), 1)
        self.assertEqual(so.state, 'sale')
        self.assertEqual(so.order_line.product_uom_qty, 2)
        self.assertEqual(so.meli_payment_method, 'visa')
        self.assertEqual(so.company_id, self.instance.company_id)

    def test_process_order_skips_duplicate(self):
        with patch.object(type(self.instance), '_register_payment_on_order'):
            self.instance._process_single_order(self._order_payload())
            self.instance._process_single_order(self._order_payload())
        so = self.env['sale.order'].search([('meli_order_id', '=', '12345')])
        self.assertEqual(len(so), 1)

    def test_process_order_skips_unpaid(self):
        order = self._order_payload(id=555, status='cancelled')
        self.instance._process_single_order(order)
        self.assertFalse(self.env['sale.order'].search([('meli_order_id', '=', '555')]))

    def test_process_order_no_matching_item(self):
        order = self._order_payload(
            id=666,
            order_items=[{'item': {'id': 'MLA_NO_EXISTE'}, 'quantity': 1, 'unit_price': 50.0}],
        )
        self.instance._process_single_order(order)
        self.assertFalse(self.env['sale.order'].search([('meli_order_id', '=', '666')]))

    # ------------------------------------------------------------------
    # Publish validation
    # ------------------------------------------------------------------

    def test_validate_before_publish_missing_category(self):
        item = self.env['meli.item'].create({
            'name': 'Sin Categoría',
            'product_id': self.product.id,
            'instance_id': self.instance.id,
            'price': 10.0,
            'available_quantity': 1,
        })
        with self.assertRaises(UserError):
            item._validate_before_publish()

    # ------------------------------------------------------------------
    # Shipment + fees capture
    # ------------------------------------------------------------------

    def test_process_order_captures_shipment_and_fees(self):
        order = self._order_payload(
            id=777,
            shipping={'id': 40001, 'logistic_type': 'me2'},
            order_items=[{
                'item': {'id': 'MLA111'}, 'quantity': 2,
                'unit_price': 100.0, 'sale_fee': 13.5,
            }],
        )
        with patch.object(type(self.instance), '_register_payment_on_order'), \
             patch.object(type(self.instance), '_fetch_seller_shipping_cost', return_value=850.0):
            self.instance._process_single_order(order)
        so = self.env['sale.order'].search([('meli_order_id', '=', '777')])
        self.assertEqual(so.meli_shipment_id, '40001')
        self.assertEqual(so.meli_logistic_type, 'me2')
        self.assertEqual(so.meli_sale_fee, 27.0)  # 13.5 x 2
        self.assertEqual(so.meli_shipping_cost, 850.0)

    # ------------------------------------------------------------------
    # Variants
    # ------------------------------------------------------------------

    def _make_variant_product(self):
        attribute = self.env['product.attribute'].create({
            'name': 'Color Test', 'meli_attribute_code': 'COLOR',
        })
        red = self.env['product.attribute.value'].create({
            'attribute_id': attribute.id, 'name': 'Rojo'})
        blue = self.env['product.attribute.value'].create({
            'attribute_id': attribute.id, 'name': 'Azul'})
        template = self.env['product.template'].create({
            'name': 'Remera Test', 'type': 'consu', 'is_storable': True,
            'attribute_line_ids': [(0, 0, {
                'attribute_id': attribute.id,
                'value_ids': [(6, 0, [red.id, blue.id])],
            })],
        })
        for i, variant in enumerate(template.product_variant_ids):
            variant.default_code = f"REM-{i}"
        return template

    def test_prepare_variations_json(self):
        template = self._make_variant_product()
        item = self.env['meli.item'].create({
            'name': 'Remera con Variantes',
            'product_id': template.id,
            'instance_id': self.instance.id,
            'price': 100.0,
            'available_quantity': 1,
        })
        self.assertTrue(item.has_variants)
        variations = item._prepare_variations_json()
        self.assertEqual(len(variations), 2)
        combo = variations[0]['attribute_combinations'][0]
        self.assertEqual(combo['id'], 'COLOR')
        self.assertIn(combo['value_name'], ('Rojo', 'Azul'))
        skus = {v['attributes'][0]['value_name'] for v in variations}
        self.assertEqual(skus, {'REM-0', 'REM-1'})

    def test_validate_variants_require_ml_code_and_sku(self):
        template = self._make_variant_product()
        template.attribute_line_ids.attribute_id.meli_attribute_code = False
        category = self.env['meli.category'].create({
            'name': 'Cat Var', 'meli_id': 'MLA999', 'is_leaf': True,
        })
        template.meli_category_id = category
        item = self.env['meli.item'].create({
            'name': 'Sin Código ML',
            'product_id': template.id,
            'instance_id': self.instance.id,
            'price': 100.0,
            'available_quantity': 1,
        })
        with self.assertRaises(UserError):
            item._validate_before_publish()

    def test_process_order_matches_by_variation(self):
        template = self._make_variant_product()
        item = self.env['meli.item'].create({
            'name': 'Remera Var',
            'product_id': template.id,
            'instance_id': self.instance.id,
            'price': 100.0,
            'available_quantity': 5,
            'meli_id': 'MLA222',
            'status': 'active',
        })
        target_variant = template.product_variant_ids[1]
        self.env['meli.item.variation'].create({
            'item_id': item.id,
            'product_id': target_variant.id,
            'variation_id': '987654',
            'sku': target_variant.default_code,
        })
        order = self._order_payload(
            id=888,
            order_items=[{
                'item': {'id': 'MLA222', 'variation_id': 987654},
                'quantity': 1, 'unit_price': 120.0,
            }],
        )
        with patch.object(type(self.instance), '_register_payment_on_order'):
            self.instance._process_single_order(order)
        so = self.env['sale.order'].search([('meli_order_id', '=', '888')])
        self.assertEqual(so.order_line.product_id, target_variant)

    def test_validate_before_publish_zero_price(self):
        category = self.env['meli.category'].create({
            'name': 'Cat Test', 'meli_id': 'MLA1234', 'is_leaf': True,
        })
        self.product.meli_category_id = category
        item = self.env['meli.item'].create({
            'name': 'Precio Cero',
            'product_id': self.product.id,
            'instance_id': self.instance.id,
            'price': 0.0,
            'available_quantity': 1,
        })
        with self.assertRaises(UserError):
            item._validate_before_publish()
