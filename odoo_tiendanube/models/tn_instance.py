from odoo import models, fields, api, _
from odoo.exceptions import UserError
import requests
import json
import datetime
import logging

_logger = logging.getLogger(__name__)

TN_API_BASE = "https://api.tiendanube.com/v1"
TN_TOKEN_URL = "https://www.tiendanube.com/apps/authorize/token"


class TnInstance(models.Model):
    _name = 'tn.instance'
    _description = 'TiendaNube Store'
    _inherit = ['mail.thread']

    name = fields.Char('Store Name', required=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', string='Compañía',
        default=lambda self: self.env.company, required=True,
    )
    app_id = fields.Char('App ID (Client ID)', required=True)
    app_secret = fields.Char('App Secret (Client Secret)', required=True)
    contact_email = fields.Char(
        'Contact Email',
        required=True,
        help='Email del desarrollador — requerido en el header User-Agent por TiendaNube',
    )

    # OAuth result — TN tokens are permanent (no expiry, no refresh needed)
    access_token = fields.Char('Access Token', readonly=True)
    tn_store_id = fields.Char('TiendaNube Store ID', readonly=True)

    authorization_code = fields.Char(
        'Authorization Code',
        help='Código devuelto por TiendaNube tras autorizar la app',
    )

    state = fields.Selection([
        ('draft', 'Draft'),
        ('authenticated', 'Authenticated'),
        ('error', 'Error'),
    ], default='draft', readonly=True, tracking=True)

    # Odoo configuration
    pricelist_id = fields.Many2one('product.pricelist', string='Pricelist (TN Prices)')
    stock_location_ids = fields.Many2many('stock.location', string='Stock Locations')
    notify_fulfillment = fields.Boolean(
        'Notificar envíos a TN', default=True,
        help='Al validar la entrega en Odoo, informar el tracking a TiendaNube '
             'para que el cliente reciba la notificación de envío.',
    )

    # Webhooks
    webhook_url = fields.Char('Webhook URL', compute='_compute_webhook_url')

    # Registered webhook IDs (JSON list) — stored to allow cleanup
    webhook_ids_json = fields.Char('Registered Webhook IDs', readonly=True)

    payment_method_ids = fields.One2many(
        'tn.payment.method', 'instance_id', string='Mapeos de Métodos de Pago'
    )

    def _mkt_log(self, operation, state, reference='', message='', payload=None,
                 res_model='', res_id=0):
        """Shortcut to the shared marketplace sync log."""
        return self.env['marketplace.sync.log'].log_event(
            'tiendanube', operation, state,
            instance_name=self.name, reference=reference, message=message,
            payload=payload, res_model=res_model, res_id=res_id,
        )

    @api.depends('tn_store_id')
    def _compute_webhook_url(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        for rec in self:
            rec.webhook_url = f"{base_url}/tiendanube/webhook"

    # -------------------------------------------------------------------------
    # OAuth
    # -------------------------------------------------------------------------

    def action_get_authorization_url(self):
        self.ensure_one()
        if not self.app_id:
            raise UserError(_("Ingresá el App ID primero."))
        url = f"https://www.tiendanube.com/apps/{self.app_id}/authorize"
        return {'type': 'ir.actions.act_url', 'url': url, 'target': 'new'}

    def action_get_token(self):
        self.ensure_one()
        if not self.authorization_code:
            raise UserError(_("Ingresá el Authorization Code devuelto por TiendaNube."))

        data = {
            'client_id': self.app_id.strip(),
            'client_secret': self.app_secret.strip(),
            'grant_type': 'authorization_code',
            'code': self.authorization_code.strip(),
        }
        try:
            resp = requests.post(TN_TOKEN_URL, json=data, timeout=30)
            _logger.info("TN OAuth response (%s): %s", resp.status_code, resp.text)
            if resp.status_code == 200:
                body = resp.json()
                self.write({
                    'access_token': body.get('access_token', '').strip(),
                    'tn_store_id': str(body.get('user_id', '')),
                    'state': 'authenticated',
                    'authorization_code': False,
                })
                self.message_post(body="Autenticación exitosa con TiendaNube.")
            else:
                self.write({'state': 'error'})
                raise UserError(_("TiendaNube rechazó la solicitud: %s") % resp.text)
        except UserError:
            raise
        except Exception as e:
            self.write({'state': 'error'})
            raise UserError(_("Error al obtener el token: %s") % str(e))

    def action_reset_connection(self):
        self.ensure_one()
        self.write({
            'state': 'draft',
            'access_token': False,
            'tn_store_id': False,
            'authorization_code': False,
            'webhook_ids_json': False,
        })

    # -------------------------------------------------------------------------
    # API wrapper
    # -------------------------------------------------------------------------

    def _get_headers(self):
        self.ensure_one()
        return {
            'Authentication': f'bearer {self.access_token}',
            'User-Agent': f'OdooMarketplaces ({self.contact_email})',
            'Content-Type': 'application/json',
        }

    def _call_api(self, method, endpoint, **kwargs):
        """
        Generic API wrapper. endpoint can be a full URL or a path like '/products'.
        TN tokens don't expire so there's no refresh logic needed.
        """
        self.ensure_one()
        if not self.access_token or not self.tn_store_id:
            raise UserError(_("La instancia '%s' no está autenticada.") % self.name)

        url = endpoint if endpoint.startswith('http') else f"{TN_API_BASE}/{self.tn_store_id}{endpoint}"
        headers = self._get_headers()
        headers.update(kwargs.pop('headers', {}))

        _logger.info("TN API %s %s", method, url)
        resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)

        if resp.status_code == 401:
            _logger.error("TN 401 for %s — token may be revoked for store %s", url, self.name)
            self.write({'state': 'error'})

        return resp

    # -------------------------------------------------------------------------
    # Webhook registration
    # -------------------------------------------------------------------------

    def action_fetch_payment_providers(self):
        """
        Fetch installed payment providers (gateways) from TiendaNube and create
        tn.payment.method records so the user can map them to Odoo journals.
        The `code` field of each provider is the `gateway` value in orders.
        """
        self.ensure_one()
        if self.state != 'authenticated':
            raise UserError(_("La instancia debe estar autenticada."))

        resp = self._call_api('GET', '/payment_providers')
        if resp.status_code != 200:
            raise UserError(_("Error al consultar gateways de pago: %s") % resp.text)

        providers = resp.json()
        created = 0
        for p in providers:
            code = (p.get('code') or p.get('id') or '').lower().strip()
            name = p.get('name') or code
            if not code:
                continue
            existing = self.env['tn.payment.method'].search([
                ('instance_id', '=', self.id),
                ('gateway_name', '=', code),
                ('payment_method_name', 'in', (False, '')),
            ], limit=1)
            if not existing:
                self.env['tn.payment.method'].create({
                    'instance_id': self.id,
                    'gateway_name': code,
                    'description': name,
                })
                created += 1

        msg = (
            f"Se importaron {created} gateways de pago nuevos de TiendaNube. "
            f"Total activos en la tienda: {len(providers)}. "
            f"Asigná un diario contable a cada uno en la pestaña 'Métodos de Pago'."
        )
        self.message_post(body=msg)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Gateways importados',
                'message': msg,
                'type': 'success',
                'sticky': False,
            },
        }

    def action_register_webhooks(self):
        """Register the required webhooks in TiendaNube for this store."""
        self.ensure_one()
        if self.state != 'authenticated':
            raise UserError(_("La instancia debe estar autenticada para registrar webhooks."))

        webhook_url = self.webhook_url
        events = [
            'order/paid',
            'order/created',
            'order/cancelled',
            'product/created',
            'product/updated',
            'product/deleted',
        ]

        registered_ids = []
        errors = []

        for event in events:
            resp = self._call_api('POST', '/webhooks', json={
                'url': webhook_url,
                'event': event,
            })
            if resp.status_code in (200, 201):
                wh_id = resp.json().get('id')
                registered_ids.append(wh_id)
                _logger.info("TN webhook registered: %s → id=%s", event, wh_id)
            elif resp.status_code == 422:
                # Already registered
                _logger.info("TN webhook already registered for event %s", event)
            else:
                errors.append(f"{event}: {resp.text}")
                _logger.error("TN webhook error for %s: %s", event, resp.text)

        if registered_ids:
            self.write({'webhook_ids_json': json.dumps(registered_ids)})

        msg = f"Webhooks registrados: {len(registered_ids)} nuevos."
        if errors:
            msg += f" Errores: {'; '.join(errors)}"
        self.message_post(body=msg)
        return True

    def action_unregister_webhooks(self):
        """Delete all registered webhooks for this store."""
        self.ensure_one()
        ids = json.loads(self.webhook_ids_json or '[]')
        for wh_id in ids:
            resp = self._call_api('DELETE', f'/webhooks/{wh_id}')
            if resp.status_code not in (200, 204):
                _logger.warning("TN: could not delete webhook %s: %s", wh_id, resp.text)
        self.write({'webhook_ids_json': False})
        self.message_post(body="Webhooks eliminados.")

    # -------------------------------------------------------------------------
    # Order processing
    # -------------------------------------------------------------------------

    def _get_or_create_partner(self, order_data):
        """Find or create partner from TN order contact + shipping address."""
        email = order_data.get('contact_email', '')
        name = order_data.get('contact_name') or email or 'TiendaNube Customer'
        phone = order_data.get('contact_phone', '')

        shipping = order_data.get('shipping_address') or {}
        street = shipping.get('address', '')
        city = shipping.get('city', '')
        zip_code = shipping.get('zipcode', '')
        country_code = shipping.get('country', '')

        partner = self.env['res.partner']
        if email:
            partner = self.env['res.partner'].search([('email', '=', email)], limit=1)
        if not partner:
            partner = self.env['res.partner'].search(
                [('tn_customer_id', '=', str(order_data.get('customer', {}).get('id', '')))],
                limit=1,
            ) if order_data.get('customer') else self.env['res.partner']

        country = self.env['res.country'].search([('code', '=', country_code)], limit=1) if country_code else False

        vals = {}
        if phone and (not partner or not partner.phone):
            vals['phone'] = phone
        if street and (not partner or not partner.street):
            vals['street'] = street
        if city and (not partner or not partner.city):
            vals['city'] = city
        if zip_code and (not partner or not partner.zip):
            vals['zip'] = zip_code
        if country and (not partner or not partner.country_id):
            vals['country_id'] = country.id

        customer_id = str(order_data.get('customer', {}).get('id', '')) if order_data.get('customer') else ''
        if customer_id:
            vals['tn_customer_id'] = customer_id

        if partner:
            if vals:
                partner.write(vals)
        else:
            vals.update({'name': name, 'email': email})
            partner = self.env['res.partner'].create(vals)

        return partner

    def _process_single_order(self, order_data):
        """
        Create a confirmed sale.order from a TiendaNube order dict.
        Skips if already imported. Only processes payment_status=paid.
        Reserves stock and registers payment automatically.
        """
        tn_order_id = str(order_data.get('id', ''))
        if not tn_order_id:
            return

        if self.env['sale.order'].search([('tn_order_id', '=', tn_order_id)], limit=1):
            return

        payment_status = order_data.get('payment_status', '')
        if payment_status != 'paid':
            _logger.info("TN order %s skipped — payment_status=%s", tn_order_id, payment_status)
            return

        partner = self._get_or_create_partner(order_data)

        order_lines = []
        for item in order_data.get('products', []):
            tn_variant_id = str(item.get('variant_id') or '')
            tn_product_id = str(item.get('product_id') or '')
            qty = float(item.get('quantity') or 1)
            price = float(item.get('price') or 0)

            # Match by variant first, then product
            variant_rec = self.env['tn.variant'].search(
                [('tn_variant_id', '=', tn_variant_id), ('instance_id', '=', self.id)],
                limit=1,
            ) if tn_variant_id else self.env['tn.variant']

            if variant_rec and variant_rec.product_id:
                odoo_product = variant_rec.product_id
            else:
                product_rec = self.env['tn.product'].search(
                    [('tn_product_id', '=', tn_product_id), ('instance_id', '=', self.id)],
                    limit=1,
                )
                odoo_product = (
                    product_rec.product_id.product_variant_id
                    if product_rec and product_rec.product_id
                    else False
                )

            if odoo_product:
                order_lines.append((0, 0, {
                    'product_id': odoo_product.id,
                    'product_uom_qty': qty,
                    'price_unit': price,
                }))
            else:
                _logger.warning(
                    "TN order %s: product/variant %s/%s not found — line skipped",
                    tn_order_id, tn_product_id, tn_variant_id,
                )

        if not order_lines:
            _logger.warning("TN order %s has no matching products — order not created", tn_order_id)
            self._mkt_log(
                'order_webhook', 'error', reference=tn_order_id,
                message='Ningún producto del pedido pudo matchearse con productos TN de Odoo.',
                payload=order_data,
            )
            return

        gateway = (order_data.get('gateway') or '').lower().strip()
        payment_method = ((order_data.get('payment_details') or {}).get('method') or '').lower().strip()

        so_vals = {
            'partner_id': partner.id,
            'tn_order_id': tn_order_id,
            'tn_instance_id': self.id,
            'tn_gateway': gateway,
            'tn_payment_method': payment_method,
            'company_id': self.company_id.id,
            'order_line': order_lines,
        }
        if self.pricelist_id:
            so_vals['pricelist_id'] = self.pricelist_id.id

        so = self.env['sale.order'].create(so_vals)
        so.action_confirm()
        _logger.info("Created SO %s for TN order %s (store: %s)", so.name, tn_order_id, self.name)
        self._mkt_log(
            'order_webhook', 'success', reference=tn_order_id,
            message=f"SO {so.name} creado",
            res_model='sale.order', res_id=so.id,
        )

        # Register payment
        self._register_payment_on_order(so, order_data)

    def _resolve_payment_journal(self, order_data):
        """
        Resolve the Odoo journal to use for this order based on its TN gateway.
        Lookup order: specific gateway+method → gateway only → instance default → any bank/cash
        """
        gateway = (order_data.get('gateway') or '').lower().strip()
        method = ((order_data.get('payment_details') or {}).get('method') or '').lower().strip()

        if gateway:
            # Try exact match: gateway + method
            mapping = self.env['tn.payment.method']
            if method:
                mapping = mapping.search([
                    ('instance_id', '=', self.id),
                    ('gateway_name', '=ilike', gateway),
                    ('payment_method_name', '=ilike', method),
                ], limit=1)
            if not mapping:
                # Fallback: gateway only (method left empty in the mapping)
                mapping = self.env['tn.payment.method'].search([
                    ('instance_id', '=', self.id),
                    ('gateway_name', '=ilike', gateway),
                    ('payment_method_name', 'in', (False, '')),
                ], limit=1)
            if mapping:
                _logger.info(
                    "TN payment: gateway=%s method=%s → journal=%s",
                    gateway, method, mapping.journal_id.name,
                )
                return mapping.journal_id

        # No mapping configured — fall back to first bank/cash journal
        journal = self.env['account.journal'].search(
            [('type', 'in', ('bank', 'cash')), ('company_id', '=', self.company_id.id)],
            limit=1,
        )
        if not journal:
            _logger.warning("TN: no bank/cash journal found for store %s", self.name)
        else:
            _logger.warning(
                "TN: no payment mapping for gateway='%s' in store %s — using fallback journal '%s'",
                gateway, self.name, journal.name,
            )
        return journal

    def _register_payment_on_order(self, so, order_data):
        """
        Create and post the invoice, then register the payment.

        The payment amount is the invoice residual — not the TN order total —
        so the invoice is always fully reconciled even when the order total
        includes shipping or discounts that are not invoice lines. Discrepancies
        are logged for review.
        """
        try:
            so._create_invoices()
            invoice = so.invoice_ids.filtered(lambda i: i.state == 'draft')[:1]
            if not invoice:
                _logger.warning("TN: no draft invoice after _create_invoices for SO %s", so.name)
                return
            invoice.action_post()

            amount = invoice.amount_residual
            if amount <= 0:
                _logger.warning("TN: invoice residual is 0 for SO %s — skipping payment", so.name)
                return

            order_total = float(order_data.get('total') or 0)
            if order_total and abs(order_total - amount) > 0.01:
                _logger.warning(
                    "TN order %s: order total %.2f differs from invoice %.2f "
                    "(shipping/discounts) — registering invoice amount",
                    so.tn_order_id, order_total, amount,
                )

            journal = self._resolve_payment_journal(order_data)
            if not journal:
                return

            self.env['account.payment.register'].with_context(
                active_model='account.move',
                active_ids=invoice.ids,
            ).create({
                'amount': amount,
                'journal_id': journal.id,
                'payment_date': fields.Date.today(),
                'communication': f"TN {so.tn_order_id}",
            }).action_create_payments()
            _logger.info(
                "Payment registered for SO %s (TN order %s) via journal '%s'",
                so.name, so.tn_order_id, journal.name,
            )
        except Exception as e:
            _logger.error("TN: could not register payment for SO %s: %s", so.name, str(e))
            self._mkt_log(
                'payment', 'error', reference=so.tn_order_id,
                message=f"No se pudo registrar el pago de {so.name}: {e}",
                res_model='sale.order', res_id=so.id,
            )

    # -------------------------------------------------------------------------
    # Catalog import
    # -------------------------------------------------------------------------

    @staticmethod
    def _tn_name(value):
        """TN returns localized dicts like {'es': '...'}; extract a plain string."""
        if isinstance(value, dict):
            return value.get('es') or next(iter(value.values()), '')
        return str(value or '')

    def _import_create_template(self, tn_data):
        """
        Create an Odoo product.template from a TN product payload, including
        attributes/variants when the TN product has more than one variant.
        Returns the created template.
        """
        name = self._tn_name(tn_data.get('name'))
        variants = tn_data.get('products') or tn_data.get('variants') or []
        attr_names = [self._tn_name(a) for a in (tn_data.get('attributes') or [])]

        tmpl_vals = {
            'name': name or f"TN {tn_data.get('id')}",
            'type': 'consu',
            'is_storable': True,
            'description_sale': self._tn_name(tn_data.get('description')),
        }

        if len(variants) > 1 and attr_names:
            # Build attribute lines: for each attribute position, collect the
            # distinct values across variants
            attribute_lines = []
            for idx, attr_name in enumerate(attr_names):
                attribute = self.env['product.attribute'].search(
                    [('name', '=ilike', attr_name)], limit=1,
                ) or self.env['product.attribute'].create({'name': attr_name})

                value_names = []
                for v in variants:
                    values = v.get('values') or []
                    if idx < len(values):
                        vname = self._tn_name(values[idx])
                        if vname and vname not in value_names:
                            value_names.append(vname)

                value_ids = []
                for vname in value_names:
                    val = self.env['product.attribute.value'].search([
                        ('attribute_id', '=', attribute.id), ('name', '=ilike', vname),
                    ], limit=1) or self.env['product.attribute.value'].create({
                        'attribute_id': attribute.id, 'name': vname,
                    })
                    value_ids.append(val.id)

                if value_ids:
                    attribute_lines.append((0, 0, {
                        'attribute_id': attribute.id,
                        'value_ids': [(6, 0, value_ids)],
                    }))
            if attribute_lines:
                tmpl_vals['attribute_line_ids'] = attribute_lines

        template = self.env['product.template'].create(tmpl_vals)

        # Assign SKU per generated variant by matching attribute value names
        if len(variants) > 1:
            for odoo_variant in template.product_variant_ids:
                odoo_value_names = set(
                    odoo_variant.product_template_attribute_value_ids
                    .mapped('product_attribute_value_id.name')
                )
                for v in variants:
                    tn_value_names = {self._tn_name(x) for x in (v.get('values') or [])}
                    if tn_value_names and tn_value_names == odoo_value_names:
                        if v.get('sku'):
                            odoo_variant.default_code = v['sku']
                        break
        elif variants:
            v = variants[0]
            if v.get('sku'):
                template.default_code = v['sku']
            if v.get('price'):
                template.list_price = float(v['price'])

        return template

    def action_import_products(self):
        """Import the store's existing products from TiendaNube into Odoo."""
        self.ensure_one()
        if self.state != 'authenticated':
            raise UserError(_("La instancia debe estar autenticada."))

        TnProduct = self.env['tn.product']
        created = matched = updated = errors = 0
        page = 1

        while True:
            resp = self._call_api('GET', '/products', params={'per_page': 50, 'page': page})
            if resp.status_code != 200:
                raise UserError(_("Error consultando productos de TN: %s") % resp.text)
            products = resp.json()
            if not products:
                break

            for tn_data in products:
                tn_id = str(tn_data.get('id'))
                try:
                    tn_product = TnProduct.search([
                        ('tn_product_id', '=', tn_id),
                        ('instance_id', '=', self.id),
                    ], limit=1)
                    variants = tn_data.get('variants') or []

                    if tn_product:
                        tn_product._sync_variants_from_response(variants)
                        updated += 1
                        continue

                    # Match Odoo template by first variant SKU, then by name
                    name = self._tn_name(tn_data.get('name'))
                    template = False
                    first_sku = variants[0].get('sku') if variants else ''
                    if first_sku:
                        odoo_variant = self.env['product.product'].search(
                            [('default_code', '=', first_sku)], limit=1)
                        template = odoo_variant.product_tmpl_id if odoo_variant else False
                    if not template and name:
                        template = self.env['product.template'].search(
                            [('name', '=ilike', name)], limit=1)
                    if template:
                        matched += 1
                    else:
                        template = self._import_create_template(tn_data)
                        created += 1

                    # Map TN categories already synced for this store
                    category_ids = []
                    for cat_id in (tn_data.get('categories') or []):
                        cid = cat_id.get('id') if isinstance(cat_id, dict) else cat_id
                        cat = self.env['tn.category'].search([
                            ('instance_id', '=', self.id),
                            ('tn_category_id', '=', str(cid)),
                        ], limit=1)
                        if cat:
                            category_ids.append(cat.id)

                    tn_product = TnProduct.create({
                        'name': name or template.name,
                        'instance_id': self.id,
                        'product_id': template.id,
                        'tn_product_id': tn_id,
                        'tn_url': tn_data.get('canonical_url', ''),
                        'status': 'active',
                        'category_ids': [(6, 0, category_ids)],
                    })
                    tn_product._sync_variants_from_response(variants)

                except Exception as e:
                    errors += 1
                    _logger.error("TN import: error on product %s: %s", tn_id, e)
                    self._mkt_log(
                        'import', 'error', reference=tn_id,
                        message=f"Error importando producto: {e}", payload=tn_data,
                    )

            if len(products) < 50:
                break
            page += 1

        msg = (
            f"Importación de catálogo TN finalizada: {created} productos creados, "
            f"{matched} vinculados a productos existentes, {updated} actualizados, "
            f"{errors} errores."
        )
        self.message_post(body=msg)
        self._mkt_log('import', 'success' if not errors else 'error', message=msg)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Importación de catálogo'),
                'message': msg,
                'type': 'success' if not errors else 'warning',
                'sticky': bool(errors),
            },
        }

    # -------------------------------------------------------------------------
    # Categories
    # -------------------------------------------------------------------------

    def action_sync_categories(self):
        """Fetch the store's category tree from TiendaNube."""
        self.ensure_one()
        if self.state != 'authenticated':
            raise UserError(_("La instancia debe estar autenticada."))

        Category = self.env['tn.category']
        fetched = []          # (tn_id, name, parent_tn_id)
        page = 1
        while True:
            resp = self._call_api('GET', '/categories', params={'per_page': 50, 'page': page})
            if resp.status_code != 200:
                raise UserError(_("Error consultando categorías: %s") % resp.text)
            cats = resp.json()
            if not cats:
                break
            for c in cats:
                name = c.get('name') or {}
                name_str = name.get('es') or next(iter(name.values()), '') if isinstance(name, dict) else str(name)
                fetched.append((str(c.get('id')), name_str, str(c.get('parent') or '') or None))
            if len(cats) < 50:
                break
            page += 1

        # First pass: create/update all categories without parent
        by_tn_id = {}
        created = 0
        for tn_id, name_str, _parent in fetched:
            cat = Category.search([
                ('instance_id', '=', self.id), ('tn_category_id', '=', tn_id),
            ], limit=1)
            if cat:
                cat.write({'name': name_str})
            else:
                cat = Category.create({
                    'instance_id': self.id, 'tn_category_id': tn_id, 'name': name_str,
                })
                created += 1
            by_tn_id[tn_id] = cat

        # Second pass: link parents
        for tn_id, _name, parent_tn_id in fetched:
            if parent_tn_id and parent_tn_id in by_tn_id:
                by_tn_id[tn_id].parent_id = by_tn_id[parent_tn_id]

        self.message_post(
            body=f"Categorías sincronizadas: {len(fetched)} en total, {created} nuevas."
        )
        return True

    # -------------------------------------------------------------------------
    # Fulfillment notification
    # -------------------------------------------------------------------------

    def _notify_fulfillment(self, picking):
        """
        Notify TiendaNube that the order was shipped, with tracking info.
        Tries the fulfillments endpoint first; on 404/405 (stores on the older
        API) falls back to pack + fulfill.
        """
        self.ensure_one()
        so = picking.sale_id
        if not so or not so.tn_order_id:
            return False

        tracking = picking.carrier_tracking_ref or ''
        body = {
            'shipping_tracking_number': tracking,
            'notify_customer': True,
        }
        try:
            resp = self._call_api('POST', f'/orders/{so.tn_order_id}/fulfillments', json=body)
            if resp.status_code in (404, 405):
                # Older API: pack then fulfill
                self._call_api('POST', f'/orders/{so.tn_order_id}/pack')
                resp = self._call_api('POST', f'/orders/{so.tn_order_id}/fulfill', json=body)

            if resp.status_code in (200, 201):
                picking.message_post(
                    body=_("Envío notificado a TiendaNube")
                         + (f" — Tracking: {tracking}" if tracking else "")
                )
                self._mkt_log(
                    'fulfillment', 'success', reference=so.tn_order_id,
                    message=f"Tracking informado: {tracking or '(sin tracking)'}",
                    res_model='stock.picking', res_id=picking.id,
                )
                return True

            _logger.error(
                "TN fulfillment error for order %s (%s): %s",
                so.tn_order_id, resp.status_code, resp.text,
            )
            self._mkt_log(
                'fulfillment', 'error', reference=so.tn_order_id,
                message=f"Error notificando envío ({resp.status_code}): {resp.text[:500]}",
                res_model='stock.picking', res_id=picking.id,
            )
        except Exception as e:
            _logger.error("TN fulfillment exception for order %s: %s", so.tn_order_id, e)
            self._mkt_log(
                'fulfillment', 'error', reference=so.tn_order_id,
                message=f"Excepción notificando envío: {e}",
                res_model='stock.picking', res_id=picking.id,
            )
        return False

    # -------------------------------------------------------------------------
    # Cron: sync orders (fallback polling — webhook is primary)
    # -------------------------------------------------------------------------

    def action_sync_orders(self, days_back=30):
        """Fetch paid orders created in the last `days_back` days from TiendaNube API."""
        created_at_min = (
            fields.Datetime.now() - datetime.timedelta(days=days_back)
        ).strftime('%Y-%m-%dT00:00:00+00:00')

        for rec in self:
            if rec.state != 'authenticated':
                continue
            try:
                page = 1
                while True:
                    resp = rec._call_api('GET', '/orders', params={
                        'payment_status': 'paid',
                        'created_at_min': created_at_min,
                        'per_page': 50,
                        'page': page,
                    })
                    if resp.status_code != 200:
                        _logger.error("TN sync orders error for %s: %s", rec.name, resp.text)
                        break
                    orders = resp.json()
                    if not orders:
                        break
                    for order in orders:
                        rec._process_single_order(order)
                    if len(orders) < 50:
                        break
                    page += 1
            except Exception as e:
                _logger.error("TN sync orders exception for %s: %s", rec.name, str(e))
                if not self.env.context.get('cron_mode'):
                    raise

    @api.model
    def cron_sync_orders(self):
        self.search([('state', '=', 'authenticated')]).with_context(cron_mode=True).action_sync_orders()
