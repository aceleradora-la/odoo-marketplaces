from odoo import models, fields, api, _
from odoo.exceptions import UserError
import requests
import datetime
import logging

_logger = logging.getLogger(__name__)

_MELI_SITES = {
    'MLA': {'name': 'Argentina', 'auth_domain': 'auth.mercadolibre.com.ar', 'currency': 'ARS'},
    'MLB': {'name': 'Brasil',    'auth_domain': 'auth.mercadolibre.com.br', 'currency': 'BRL'},
    'MLC': {'name': 'Chile',     'auth_domain': 'auth.mercadolibre.cl',     'currency': 'CLP'},
    'MLM': {'name': 'México',    'auth_domain': 'auth.mercadolibre.com.mx', 'currency': 'MXN'},
    'MLU': {'name': 'Uruguay',   'auth_domain': 'auth.mercadolibre.com.uy', 'currency': 'UYU'},
    'MCO': {'name': 'Colombia',  'auth_domain': 'auth.mercadolibre.com.co', 'currency': 'COP'},
    'MPE': {'name': 'Perú',      'auth_domain': 'auth.mercadolibre.com.pe', 'currency': 'PEN'},
    'MLV': {'name': 'Venezuela', 'auth_domain': 'auth.mercadolibre.com.ve', 'currency': 'VES'},
}


class MeliInstance(models.Model):
    _name = 'meli.instance'
    _description = 'MercadoLibre Instance'
    _inherit = ['mail.thread']

    name = fields.Char('Account Name', required=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', string='Compañía',
        default=lambda self: self.env.company, required=True,
    )
    site_id = fields.Selection([
        ('MLA', 'Argentina'),
        ('MLB', 'Brasil'),
        ('MLC', 'Chile'),
        ('MLM', 'México'),
        ('MLU', 'Uruguay'),
        ('MCO', 'Colombia'),
        ('MPE', 'Perú'),
        ('MLV', 'Venezuela'),
    ], string='País / Site', default='MLA', required=True)
    currency_ml = fields.Char('Moneda ML', compute='_compute_currency_ml', store=False)

    app_id = fields.Char('App ID', required=True)
    secret_key = fields.Char('Secret Key', required=True)
    redirect_uri = fields.Char(
        'Redirect URI', required=True,
        help='The exact redirect URI configured in MercadoLibre App',
        default=lambda self: self.env['ir.config_parameter'].sudo().get_param('web.base.url') + '/meli/auth'
    )

    authorization_code = fields.Char(
        'Authorization Code',
        help='Code returned by MercadoLibre after user authorizes the app'
    )
    access_token = fields.Char('Access Token', readonly=True)
    refresh_token = fields.Char('Refresh Token', readonly=True)
    token_expiration = fields.Datetime('Token Expiration', readonly=True)

    pricelist_id = fields.Many2one('product.pricelist', string='Pricelist (ML Prices)')
    stock_location_ids = fields.Many2many('stock.location', string='Stock Locations')
    payment_method_ids = fields.One2many(
        'meli.payment.method', 'instance_id', string='Mapeos de Métodos de Pago'
    )
    warehouse_full_id = fields.Many2one(
        'stock.warehouse',
        string='Almacén ML FULL',
        help='Almacén de Odoo que representa el stock en los centros de fulfillment de MercadoLibre. '
             'Los pedidos FULL se despachan desde aquí y se validan automáticamente.',
    )

    # Fee billing configuration
    create_fee_bill = fields.Boolean(
        'Crear factura de comisiones',
        help='Al procesar cada pedido, crear una factura de proveedor en borrador con la '
             'comisión de ML y el costo de envío del vendedor.',
    )
    fee_product_id = fields.Many2one(
        'product.product', string='Producto Comisión ML',
        domain="[('type', '=', 'service')]",
        help='Producto de servicio usado en la línea de comisión de la factura de proveedor.',
    )
    shipping_cost_product_id = fields.Many2one(
        'product.product', string='Producto Costo de Envío',
        domain="[('type', '=', 'service')]",
        help='Producto de servicio usado en la línea de costo de envío.',
    )
    fee_partner_id = fields.Many2one(
        'res.partner', string='Proveedor ML',
        help='Partner proveedor de las facturas de comisiones (ej: MercadoLibre SRL).',
    )
    seller_id = fields.Char('Seller ID', readonly=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('authenticated', 'Authenticated'),
        ('error', 'Error')
    ], string='Status', default='draft', readonly=True)

    webhook_url = fields.Char(
        'Webhook URL',
        compute='_compute_webhook_url',
        help='Configure esta URL en el Panel de Desarrolladores de MercadoLibre → Notificaciones',
    )

    def _mkt_log(self, operation, state, reference='', message='', payload=None,
                 res_model='', res_id=0):
        """Shortcut to the shared marketplace sync log."""
        return self.env['marketplace.sync.log'].log_event(
            'meli', operation, state,
            instance_name=self.name, reference=reference, message=message,
            payload=payload, res_model=res_model, res_id=res_id,
        )

    @api.depends('site_id')
    def _compute_webhook_url(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        for rec in self:
            rec.webhook_url = f"{base_url}/meli/webhook"

    @api.depends('site_id')
    def _compute_currency_ml(self):
        for rec in self:
            rec.currency_ml = _MELI_SITES.get(rec.site_id or 'MLA', {}).get('currency', 'ARS')

    def action_get_authorization_url(self):
        self.ensure_one()
        if not self.app_id or not self.redirect_uri:
            raise UserError(_("Please configure App ID and Redirect URI first."))

        auth_domain = _MELI_SITES.get(self.site_id, {}).get('auth_domain', 'auth.mercadolibre.com.ar')
        url = (
            f"https://{auth_domain}/authorization"
            f"?response_type=code&client_id={self.app_id}&redirect_uri={self.redirect_uri}"
            f"&access_type=offline"
        )
        return {
            'type': 'ir.actions.act_url',
            'url': url,
            'target': 'new',
        }

    def action_get_token(self):
        self.ensure_one()
        if not self.authorization_code:
            raise UserError(_("Please provide the Authorization Code generated by MercadoLibre."))

        url = "https://api.mercadolibre.com/oauth/token"
        headers = {
            'accept': 'application/json',
            'content-type': 'application/x-www-form-urlencoded'
        }
        data = {
            'grant_type': 'authorization_code',
            'client_id': self.app_id.strip(),
            'client_secret': self.secret_key.strip(),
            'code': self.authorization_code.strip(),
            'redirect_uri': self.redirect_uri.strip(),
        }

        try:
            _logger.info("Requesting ML token for %s with App ID %s...", self.name, self.app_id.strip()[:5])
            response = requests.post(url, headers=headers, data=data)
            _logger.info("ML OAuth Response (%s): %s", response.status_code, response.text)
            self._process_token_response(response)
        except Exception as e:
            self.write({'state': 'error'})
            _logger.error("Error requesting ML token: %s", str(e))
            raise UserError(_("Error requesting token: %s") % str(e))

    def action_refresh_token(self):
        for rec in self:
            if not rec.refresh_token:
                msg = "Cannot refresh token for %s: No refresh_token found!" % rec.name
                _logger.error(msg)
                if not self.env.context.get('cron_mode'):
                    raise UserError(_(msg))
                continue

            url = "https://api.mercadolibre.com/oauth/token"
            headers = {
                'accept': 'application/json',
                'content-type': 'application/x-www-form-urlencoded'
            }
            data = {
                'grant_type': 'refresh_token',
                'client_id': rec.app_id.strip(),
                'client_secret': rec.secret_key.strip(),
                'refresh_token': rec.refresh_token.strip(),
            }

            try:
                _logger.info("Refreshing ML token for %s...", rec.name)
                response = requests.post(url, headers=headers, data=data)
                _logger.info("ML Refresh Response (%s): %s", response.status_code, response.text)
                rec._process_token_response(response)
            except Exception as e:
                rec.write({'state': 'error'})
                _logger.error("Error refreshing ML token for %s: %s", rec.name, str(e))
                if not self.env.context.get('cron_mode'):
                    raise UserError(_("Error refreshing token: %s") % str(e))

    def _process_token_response(self, response):
        if response.status_code == 200:
            res_data = response.json()
            expires_in = res_data.get('expires_in', 21600)
            user_id = str(res_data.get('user_id', ''))
            access_token = res_data.get('access_token', '').strip()
            new_refresh_token = res_data.get('refresh_token', '').strip()

            # Preserve existing refresh_token if ML doesn't return a new one
            # (ML only returns refresh_token on initial auth with access_type=offline)
            vals = {
                'access_token': access_token,
                'token_expiration': fields.Datetime.now() + datetime.timedelta(seconds=expires_in),
                'state': 'authenticated',
                'authorization_code': False,
            }
            if new_refresh_token:
                vals['refresh_token'] = new_refresh_token
                _logger.info("ML OAuth: refresh_token received and saved for %s", self.name)
            else:
                _logger.warning(
                    "ML OAuth: no refresh_token in response for %s. "
                    "Ensure the ML app has 'offline_access' enabled and re-authorize with access_type=offline.",
                    self.name
                )
            if user_id:
                vals['seller_id'] = user_id

            self.sudo().write(vals)
        else:
            error_msg = response.text
            self.write({'state': 'error'})
            _logger.error("ML OAuth Error: %s", error_msg)
            raise UserError(_("MercadoLibre rejected the request: %s") % error_msg)

    def action_reset_connection(self):
        self.ensure_one()
        self.write({
            'state': 'draft',
            'authorization_code': False,
            'access_token': False,
            'refresh_token': False,
            'token_expiration': False,
            'seller_id': False,
        })

    def _call_api(self, method, url, **kwargs):
        """
        Generic API wrapper with automatic token validation and retry on 401.
        """
        self.ensure_one()
        self.check_token_validity()

        headers = kwargs.get('headers') or {}
        headers['Authorization'] = f'Bearer {self.access_token}'
        kwargs['headers'] = headers

        _logger.info("ML API Call: %s %s", method, url)
        response = requests.request(method, url, **kwargs)

        if response.status_code == 401:
            _logger.warning("401 Unauthorized for %s — refreshing token for %s...", url, self.name)

            try:
                self.sudo().action_refresh_token()
            except Exception as e:
                _logger.error("Token refresh exception for %s: %s", self.name, str(e))
                return response

            # Force re-read from DB after refresh
            self.invalidate_recordset(['access_token', 'refresh_token'])
            new_token = self.sudo().access_token

            if not new_token:
                _logger.error("Token refresh FAILED for %s: no token after refresh.", self.name)
                return response

            _logger.info("Retrying ML API Call with refreshed token for %s...", self.name)
            headers['Authorization'] = f'Bearer {new_token}'
            kwargs['headers'] = headers
            response = requests.request(method, url, **kwargs)

            if response.status_code == 401:
                _logger.error("STILL 401 after token refresh for %s: %s", self.name, response.text)

        return response

    def check_token_validity(self):
        """Validate token before API requests, refreshing if expiring within 5 minutes."""
        self.ensure_one()
        if not self.access_token:
            raise UserError(_("Not authenticated with MercadoLibre Account '%s'.") % self.name)

        limit_time = fields.Datetime.now() + datetime.timedelta(minutes=5)
        if self.token_expiration and self.token_expiration <= limit_time:
            self.action_refresh_token()

    def cron_refresh_tokens(self):
        """Proactively refresh tokens expiring in the next 30 minutes."""
        limit_time = fields.Datetime.now() + datetime.timedelta(minutes=30)
        instances = self.search([
            ('state', '=', 'authenticated'),
            ('token_expiration', '<=', limit_time),
        ])
        instances.with_context(cron_mode=True).action_refresh_token()

    def action_sync_root_categories(self):
        self.ensure_one()
        url = f"https://api.mercadolibre.com/sites/{self.site_id}/categories"
        response = self._call_api('GET', url)
        if response.status_code == 200:
            for cat in response.json():
                if not self.env['meli.category'].search([('meli_id', '=', cat['id'])]):
                    self.env['meli.category'].create({
                        'name': cat['name'],
                        'meli_id': cat['id'],
                    })
        else:
            raise UserError(_("Error syncing categories: %s") % response.text)

    def _ensure_seller_id(self):
        """Fetch and cache seller_id if not already set. Returns seller_id or None."""
        self.ensure_one()
        if self.seller_id:
            return self.seller_id
        try:
            resp = self._call_api('GET', "https://api.mercadolibre.com/users/me")
            if resp.status_code == 200:
                seller_id = str(resp.json().get('id'))
                self.sudo().write({'seller_id': seller_id})
                return seller_id
            _logger.error("Failed to fetch ML seller info for %s: %s", self.name, resp.text)
        except Exception as e:
            _logger.error("Exception fetching seller info for %s: %s", self.name, str(e))
        return None

    def action_fetch_payment_methods(self):
        """
        Fetch all payment methods for this site from ML (public endpoint, no token needed)
        and create/update meli.payment.method records so the user can map them to journals.
        """
        self.ensure_one()
        url = f"https://api.mercadolibre.com/sites/{self.site_id}/payment_methods"
        # This endpoint is public but we use _call_api for consistency
        resp = self._call_api('GET', url)
        if resp.status_code != 200:
            raise UserError(_("Error al consultar métodos de pago de ML: %s") % resp.text)

        methods = resp.json()
        created = 0
        for m in methods:
            mid = m.get('id', '')
            mtype = m.get('payment_type_id', '')
            mname = m.get('name', mid)
            existing = self.env['meli.payment.method'].search([
                ('instance_id', '=', self.id),
                ('payment_method_id', '=', mid),
            ], limit=1)
            if not existing:
                self.env['meli.payment.method'].create({
                    'instance_id': self.id,
                    'payment_method_id': mid,
                    'payment_type_id': mtype,
                    'description': mname,
                })
                created += 1

        msg = (
            f"Se importaron {created} métodos de pago nuevos de MercadoLibre ({self.site_id}). "
            f"Total disponibles: {len(methods)}. "
            f"Asigná un diario contable a cada uno en la pestaña 'Métodos de Pago'."
        )
        self.message_post(body=msg)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Métodos de pago importados',
                'message': msg,
                'type': 'success',
                'sticky': False,
            },
        }

    def _resolve_payment_journal(self, payments, company):
        """
        Resolve the Odoo journal from the ML order payments list.
        payments: list of dicts with payment_method_id and payment_type_id.
        Lookup: specific method_id → type fallback → first bank/cash journal of the company.
        """
        for p in payments:
            if p.get('status') not in ('approved', 'in_process', None):
                continue
            method_id = (p.get('payment_method_id') or '').lower().strip()
            type_id = (p.get('payment_type_id') or '').lower().strip()

            if method_id:
                mapping = self.env['meli.payment.method'].search([
                    ('instance_id', '=', self.id),
                    ('payment_method_id', '=ilike', method_id),
                    ('journal_id', '!=', False),
                ], limit=1)
                if mapping:
                    _logger.info("ML payment: method=%s → journal=%s", method_id, mapping.journal_id.name)
                    return mapping.journal_id

            if type_id:
                mapping = self.env['meli.payment.method'].search([
                    ('instance_id', '=', self.id),
                    ('payment_method_id', 'in', (False, '')),
                    ('payment_type_id', '=ilike', type_id),
                    ('journal_id', '!=', False),
                ], limit=1)
                if mapping:
                    _logger.info("ML payment: type=%s → journal=%s", type_id, mapping.journal_id.name)
                    return mapping.journal_id

        journal = self.env['account.journal'].search(
            [('type', 'in', ('bank', 'cash')), ('company_id', '=', company.id)],
            limit=1,
        )
        if journal:
            _logger.warning(
                "ML: no payment mapping for this order on instance %s — fallback to '%s'",
                self.name, journal.name,
            )
        return journal

    def _register_payment_on_order(self, so, order):
        """
        Create and post the invoice, then register the payment against it.

        The payment amount is the invoice residual — not the ML gateway total —
        so the invoice is always fully reconciled even when the gateway total
        includes shipping or fees that are not invoice lines. Discrepancies
        against the gateway total are logged for review.
        """
        try:
            so._create_invoices()
            invoice = so.invoice_ids.filtered(lambda i: i.state == 'draft')[:1]
            if not invoice:
                _logger.warning("ML: no draft invoice after _create_invoices for SO %s", so.name)
                return

            # Propagate ML references so the invoice-upload cron can find it
            invoice.write({
                'meli_order_id': so.meli_order_id,
                'meli_instance_id': self.id,
            })
            invoice.action_post()

            amount = invoice.amount_residual
            if amount <= 0:
                _logger.warning("ML: invoice residual is 0 for SO %s — skipping payment", so.name)
                return

            payments = order.get('payments') or []
            gateway_total = sum(
                float(p.get('total_paid_amount') or 0)
                for p in payments if p.get('status') == 'approved'
            )
            if gateway_total and abs(gateway_total - amount) > 0.01:
                _logger.warning(
                    "ML order %s: gateway total %.2f differs from invoice %.2f "
                    "(shipping/fees) — registering invoice amount",
                    so.meli_order_id, gateway_total, amount,
                )

            journal = self._resolve_payment_journal(payments, so.company_id)
            if not journal:
                return

            self.env['account.payment.register'].with_context(
                active_model='account.move',
                active_ids=invoice.ids,
            ).create({
                'amount': amount,
                'journal_id': journal.id,
                'payment_date': fields.Date.today(),
                'communication': f"ML {so.meli_order_id}",
            }).action_create_payments()
            _logger.info(
                "ML payment registered for SO %s (order %s) via journal '%s'",
                so.name, so.meli_order_id, journal.name,
            )
        except Exception as e:
            _logger.error("ML: could not register payment for SO %s: %s", so.name, str(e))
            self._mkt_log(
                'payment', 'error', reference=so.meli_order_id,
                message=f"No se pudo registrar el pago de {so.name}: {e}",
                res_model='sale.order', res_id=so.id,
            )

    def _get_or_create_partner(self, buyer):
        """Find or create a res.partner from ML buyer data, enriching existing records."""
        meli_user_id = str(buyer.get('id', ''))
        nickname = buyer.get('nickname') or 'MercadoLibre Guest'
        email = buyer.get('email', '')
        phone = buyer.get('phone', {}).get('number', '') if isinstance(buyer.get('phone'), dict) else ''

        # Shipping address from buyer (available in some ML order payloads)
        shipping = buyer.get('shipping_address') or {}
        street = shipping.get('address_line', '')
        city = shipping.get('city', {}).get('name', '') if isinstance(shipping.get('city'), dict) else ''
        zip_code = shipping.get('zip_code', '')

        partner = self.env['res.partner']
        if meli_user_id:
            partner = partner.search([('meli_user_id', '=', meli_user_id)], limit=1)
        if not partner and email:
            partner = self.env['res.partner'].search([('email', '=', email)], limit=1)

        # Only fill fields the partner doesn't have yet — never overwrite existing data
        vals = {'meli_user_id': meli_user_id, 'meli_nickname': nickname}
        for field_name, value in (
            ('email', email), ('phone', phone),
            ('street', street), ('city', city), ('zip', zip_code),
        ):
            if value and (not partner or not partner[field_name]):
                vals[field_name] = value

        if partner:
            partner.write(vals)
        else:
            vals['name'] = nickname
            partner = self.env['res.partner'].create(vals)

        return partner

    def _fetch_seller_shipping_cost(self, shipment_id):
        """
        Return the shipping cost paid by the seller for a shipment.
        ML: shipping_option.list_cost is the full cost, shipping_option.cost is
        what the buyer pays; the seller covers the difference (free shipping etc.).
        """
        if not shipment_id:
            return 0.0
        try:
            resp = self._call_api('GET', f"https://api.mercadolibre.com/shipments/{shipment_id}")
            if resp.status_code != 200:
                return 0.0
            option = resp.json().get('shipping_option') or {}
            list_cost = float(option.get('list_cost') or 0)
            buyer_cost = float(option.get('cost') or 0)
            return max(0.0, list_cost - buyer_cost)
        except Exception as e:
            _logger.warning("ML: could not fetch shipping cost for shipment %s: %s", shipment_id, e)
            return 0.0

    def _create_fee_bill(self, so):
        """Create a draft vendor bill with ML fee and seller shipping cost lines."""
        if not self.create_fee_bill or not self.fee_partner_id:
            return
        lines = []
        if so.meli_sale_fee > 0 and self.fee_product_id:
            lines.append((0, 0, {
                'product_id': self.fee_product_id.id,
                'name': f"Comisión ML pedido {so.meli_order_id}",
                'quantity': 1,
                'price_unit': so.meli_sale_fee,
            }))
        if so.meli_shipping_cost > 0 and self.shipping_cost_product_id:
            lines.append((0, 0, {
                'product_id': self.shipping_cost_product_id.id,
                'name': f"Costo de envío ML pedido {so.meli_order_id}",
                'quantity': 1,
                'price_unit': so.meli_shipping_cost,
            }))
        if not lines:
            return
        try:
            bill = self.env['account.move'].create({
                'move_type': 'in_invoice',
                'partner_id': self.fee_partner_id.id,
                'company_id': so.company_id.id,
                'ref': f"ML {so.meli_order_id}",
                'invoice_date': fields.Date.today(),
                'invoice_line_ids': lines,
            })
            so.message_post(body=_("Factura de comisiones ML creada en borrador: %s") % bill.display_name)
            _logger.info("ML fee bill %s created for SO %s", bill.id, so.name)
        except Exception as e:
            _logger.error("ML: could not create fee bill for SO %s: %s", so.name, e)
            self._mkt_log(
                'payment', 'error', reference=so.meli_order_id,
                message=f"No se pudo crear la factura de comisiones de {so.name}: {e}",
                res_model='sale.order', res_id=so.id,
            )

    def _is_full_order(self, order):
        """
        Detect if the ML order was fulfilled by MercadoLibre (FULL/fulfillment).
        ML marks these orders with the 'fulfillment' tag in order.tags.
        Also checks shipping.logistic_type as a fallback.
        """
        tags = order.get('tags') or []
        if 'fulfillment' in tags:
            return True
        logistic_type = (order.get('shipping') or {}).get('logistic_type', '')
        return logistic_type == 'fulfillment'

    def _process_single_order(self, order):
        """
        Create a confirmed sale.order from an ML order dict.

        FULL orders (fulfilled by MercadoLibre):
          - Use the configured warehouse_full_id
          - Auto-validate delivery (stock already at ML warehouse)
          - Register payment

        Self-fulfilled orders (seller ships):
          - Use default warehouse
          - Reserve stock only (delivery pending physical dispatch)
          - Register payment so accounting is up to date
        """
        order_id = str(order.get('id'))
        if self.env['sale.order'].search([('meli_order_id', '=', order_id)], limit=1):
            return

        ml_status = order.get('status', '')
        if ml_status not in ('paid', 'payment_required'):
            _logger.info("ML order %s skipped — status=%s", order_id, ml_status)
            return

        is_full = self._is_full_order(order)
        buyer = order.get('buyer') or {}
        partner = self._get_or_create_partner(buyer)

        order_lines = []
        for item in order.get('order_items', []):
            meli_item_id = item.get('item', {}).get('id')
            qty = item.get('quantity', 1)
            price = item.get('unit_price', 0)
            m_item = self.env['meli.item'].search([
                ('meli_id', '=', meli_item_id),
                ('instance_id', '=', self.id),
            ], limit=1)
            if m_item and m_item.product_id:
                order_lines.append((0, 0, {
                    'product_id': m_item.product_id.product_variant_id.id,
                    'product_uom_qty': qty,
                    'price_unit': price,
                }))
            else:
                _logger.warning(
                    "ML order %s: item %s not found in instance %s — line skipped",
                    order_id, meli_item_id, self.name,
                )

        if not order_lines:
            _logger.warning("ML order %s has no matching products — order not created", order_id)
            self._mkt_log(
                'order_webhook', 'error', reference=order_id,
                message='Ningún producto del pedido pudo matchearse con publicaciones de Odoo.',
                payload=order,
            )
            return

        so_vals = {
            'partner_id': partner.id,
            'meli_order_id': order_id,
            'meli_instance_id': self.id,
            'company_id': self.company_id.id,
            'order_line': order_lines,
        }
        if self.pricelist_id:
            so_vals['pricelist_id'] = self.pricelist_id.id
        if is_full and self.warehouse_full_id:
            so_vals['warehouse_id'] = self.warehouse_full_id.id

        # Capture payment method info for traceability
        payments = order.get('payments') or []
        first_payment = next((p for p in payments if p.get('status') == 'approved'), payments[0] if payments else {})
        so_vals['meli_payment_method'] = (first_payment.get('payment_method_id') or '').lower()
        so_vals['meli_payment_type'] = (first_payment.get('payment_type_id') or '').lower()

        # Capture shipment reference for tracking/labels
        shipping = order.get('shipping') or {}
        if shipping.get('id'):
            so_vals['meli_shipment_id'] = str(shipping['id'])
        if shipping.get('logistic_type'):
            so_vals['meli_logistic_type'] = shipping['logistic_type']

        # Marketplace costs: ML fee from order items, shipping cost from shipment
        so_vals['meli_sale_fee'] = sum(
            float(i.get('sale_fee') or 0) * (i.get('quantity') or 1)
            for i in order.get('order_items', [])
        )
        so_vals['meli_shipping_cost'] = self._fetch_seller_shipping_cost(shipping.get('id'))

        so = self.env['sale.order'].create(so_vals)
        so.action_confirm()

        fulfillment_label = 'FULL (ML Fulfillment)' if is_full else 'Envío propio'
        _logger.info(
            "Created SO %s for ML order %s [%s] (instance: %s)",
            so.name, order_id, fulfillment_label, self.name,
        )
        self._mkt_log(
            'order_webhook', 'success', reference=order_id,
            message=f"SO {so.name} creado [{fulfillment_label}]",
            res_model='sale.order', res_id=so.id,
        )

        if is_full:
            # Stock is physically at ML — auto-validate delivery to reflect reality
            if not self.warehouse_full_id:
                _logger.warning(
                    "ML order %s is FULL but no warehouse_full_id configured on instance %s. "
                    "Delivery will NOT be auto-validated.",
                    order_id, self.name,
                )
            else:
                try:
                    for picking in so.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel')):
                        for move in picking.move_ids:
                            move.quantity = move.product_uom_qty
                        picking.with_context(skip_immediate=True).button_validate()
                    _logger.info("Auto-validated FULL delivery for SO %s", so.name)
                except Exception as e:
                    _logger.error(
                        "Could not auto-validate FULL delivery for SO %s: %s",
                        so.name, str(e),
                    )

        if ml_status == 'paid':
            self._register_payment_on_order(so, order)

        self._create_fee_bill(so)

    def action_sync_orders(self, days_back=30):
        """Fallback polling: fetch paid orders created in the last `days_back` days."""
        date_from = (
            fields.Datetime.now() - datetime.timedelta(days=days_back)
        ).strftime('%Y-%m-%dT00:00:00.000-00:00')

        for rec in self:
            if rec.state != 'authenticated':
                continue

            seller_id = rec._ensure_seller_id()
            if not seller_id:
                continue

            try:
                orders_url = "https://api.mercadolibre.com/orders/search"
                offset = 0
                limit = 50

                while True:
                    params = {
                        'seller': seller_id,
                        'order.status': 'paid',
                        'order.date_created.from': date_from,
                        'offset': offset,
                        'limit': limit,
                    }
                    response = rec._call_api('GET', orders_url, params=params)

                    if response.status_code != 200:
                        _logger.error("Error searching orders for %s: %s", rec.name, response.text)
                        break

                    data = response.json()
                    orders = data.get('results', [])
                    if not orders:
                        break

                    for order in orders:
                        rec._process_single_order(order)

                    offset += limit
                    total = data.get('paging', {}).get('total', 0)
                    if offset >= total:
                        break

            except Exception as e:
                _logger.error("Exception while syncing orders for %s: %s", rec.name, str(e))
                if not self.env.context.get('cron_mode'):
                    raise

    @api.model
    def cron_sync_orders(self):
        instances = self.search([('state', '=', 'authenticated')])
        instances.with_context(cron_mode=True).action_sync_orders()

    def action_sync_items(self):
        self.ensure_one()
        self.env['meli.item'].with_context(
            cron_mode=self.env.context.get('cron_mode')
        ).action_import_items(self)
