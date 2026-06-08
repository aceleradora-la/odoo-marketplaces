from odoo import models, fields, api, _
from odoo.exceptions import UserError
import requests
import logging

_logger = logging.getLogger(__name__)

TN_API_BASE = "https://api.tiendanube.com/v1"
TN_TOKEN_URL = "https://www.tiendanube.com/apps/authorize/token"


class TnInstance(models.Model):
    _name = 'tn.instance'
    _description = 'TiendaNube Store'
    _inherit = ['mail.thread']

    name = fields.Char('Store Name', required=True, tracking=True)
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

    # Webhooks
    webhook_url = fields.Char('Webhook URL', compute='_compute_webhook_url')

    # Registered webhook IDs (JSON list) — stored to allow cleanup
    webhook_ids_json = fields.Char('Registered Webhook IDs', readonly=True)

    payment_method_ids = fields.One2many(
        'tn.payment.method', 'instance_id', string='Mapeos de Métodos de Pago'
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

    def action_register_webhooks(self):
        """Register the required webhooks in TiendaNube for this store."""
        self.ensure_one()
        if self.state != 'authenticated':
            raise UserError(_("La instancia debe estar autenticada para registrar webhooks."))

        webhook_url = self.webhook_url
        events = [
            'orders/paid',
            'orders/created',
            'orders/cancelled',
            'products/created',
            'products/updated',
            'products/deleted',
        ]

        import json
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
        import json
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
            return

        gateway = (order_data.get('gateway') or '').lower().strip()
        payment_method = ((order_data.get('payment_details') or {}).get('method') or '').lower().strip()

        so = self.env['sale.order'].create({
            'partner_id': partner.id,
            'tn_order_id': tn_order_id,
            'tn_instance_id': self.id,
            'tn_gateway': gateway,
            'tn_payment_method': payment_method,
            'order_line': order_lines,
        })
        so.action_confirm()
        _logger.info("Created SO %s for TN order %s (store: %s)", so.name, tn_order_id, self.name)

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
            mapping = self.env['tn.payment.method'].search([
                ('instance_id', '=', self.id),
                ('gateway_name', '=ilike', gateway),
                ('payment_method_name', '=ilike', method),
            ], limit=1)
            if not mapping:
                # Fallback: gateway only (method left empty in the mapping)
                mapping = self.env['tn.payment.method'].search([
                    ('instance_id', '=', self.id),
                    ('gateway_name', '=ilike', gateway),
                    ('payment_method_name', '=', False),
                ], limit=1)
                if not mapping:
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
            [('type', 'in', ('bank', 'cash')), ('company_id', '=', self.env.company.id)],
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
        try:
            so._create_invoices()
            invoice = so.invoice_ids.filtered(lambda i: i.state == 'draft')[:1]
            if not invoice:
                return
            invoice.action_post()

            total = float(order_data.get('total') or 0)
            if total <= 0:
                return

            journal = self._resolve_payment_journal(order_data)
            if not journal:
                return

            self.env['account.payment.register'].with_context(
                active_model='account.move',
                active_ids=invoice.ids,
            ).create({
                'amount': total,
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

    # -------------------------------------------------------------------------
    # Cron: sync orders (fallback polling — webhook is primary)
    # -------------------------------------------------------------------------

    def action_sync_orders(self):
        """Fetch recent paid orders from TiendaNube API."""
        for rec in self:
            if rec.state != 'authenticated':
                continue
            try:
                page = 1
                while True:
                    resp = rec._call_api('GET', '/orders', params={
                        'payment_status': 'paid',
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
