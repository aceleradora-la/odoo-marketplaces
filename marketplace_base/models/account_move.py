import logging
from functools import partial

from odoo import models, fields, api, SUPERUSER_ID
from odoo.modules.registry import Registry

from .push_config import ACELIO_REF_PREFIXES, acelio_dump, acelio_post

_logger = logging.getLogger(__name__)

# Facturas y notas de crédito de cliente: son las únicas que le interesan a un
# marketplace. Las de proveedor (comisiones del canal) no se avisan.
CUSTOMER_MOVE_TYPES = ('out_invoice', 'out_refund')


def _push_after_commit(dbname, config_id, url, secret, body, log_vals):
    """
    Corre DESPUÉS del commit del asiento: el POST no bloquea la validación de
    la factura y, si Acelio está caído, la factura ya quedó publicada igual.
    Abre su propio cursor porque el de la transacción original ya está cerrado.
    """
    ok, message = acelio_post(url, secret, body)
    try:
        with Registry(dbname).cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            env['marketplace.sync.log'].log_event(
                log_vals['channel'], 'invoice', 'success' if ok else 'error',
                instance_name=log_vals['instance_name'],
                reference=log_vals['reference'],
                message=("Aviso de factura a Acelio: %s" % message)[:2000],
                payload=body,
                res_model='account.move',
                res_id=log_vals['move_id'],
            )
            env['marketplace.push.config'].browse(config_id)._register_result(ok, message)
    except Exception:  # noqa: BLE001
        _logger.exception("marketplace_base: no se pudo registrar el aviso de factura a Acelio")


class AccountMove(models.Model):
    """
    Aviso a Acelio cuando se publica la factura de un pedido de marketplace.

    Es el equivalente de stock_picking._action_done para la parte fiscal: sin
    esto Acelio solo se entera de las facturas que generó él mismo, y una
    factura hecha a mano en Odoo queda invisible para el canal.

    Se engancha en `_post` y no en `write`: `state` lo escribe el propio `_post`
    y hay varios caminos (wizard de facturación, botón, cron de suscripciones)
    que terminan ahí. Es el único punto por el que pasan todas las facturas.
    """
    _inherit = 'account.move'

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        try:
            posted._marketplace_notify_acelio()
        except Exception:  # noqa: BLE001
            # Nunca romper la publicación de una factura por una notificación.
            _logger.exception("marketplace_base: fallo al preparar el aviso de factura a Acelio")
        return posted

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _marketplace_sale_orders(self):
        """Pedidos de venta de los que sale la factura."""
        self.ensure_one()
        orders = self.env['sale.order'].browse()
        lines = self.line_ids
        if 'sale_line_ids' in lines._fields:
            orders = lines.sale_line_ids.order_id
        if not orders and self.invoice_origin:
            names = [n.strip() for n in self.invoice_origin.split(',') if n.strip()]
            if names:
                orders = self.env['sale.order'].search([('name', 'in', names)])
        # Nota de crédito emitida desde la factura: hereda su pedido.
        if not orders and self.reversed_entry_id:
            orders = self.reversed_entry_id._marketplace_sale_orders()
        return orders

    def _marketplace_channel(self):
        """Canal deducido del prefijo de client_order_ref, o False si no aplica."""
        self.ensure_one()
        if self.state != 'posted' or self.move_type not in CUSTOMER_MOVE_TYPES:
            return False
        for order in self._marketplace_sale_orders():
            ref = order.client_order_ref or ''
            for prefix, channel in ACELIO_REF_PREFIXES.items():
                if ref.startswith(prefix):
                    return channel
        return False

    def _marketplace_order(self):
        """Primer pedido de marketplace de la factura, o vacío."""
        self.ensure_one()
        for order in self._marketplace_sale_orders():
            ref = order.client_order_ref or ''
            if any(ref.startswith(prefix) for prefix in ACELIO_REF_PREFIXES):
                return order
        return self.env['sale.order'].browse()

    def _marketplace_acelio_payload(self, tenant):
        """Payload del aviso, o False si la factura no es de un marketplace."""
        self.ensure_one()
        if not self._marketplace_channel():
            return False
        order = self._marketplace_order()
        if not order:
            return False
        posted_at = self.invoice_date or fields.Date.context_today(self)
        # Número fiscal de la localización (AR: 00001-00000123). Sin l10n el
        # campo no existe y alcanza con `name`.
        document_number = ''
        if 'l10n_latam_document_number' in self._fields:
            document_number = self.l10n_latam_document_number or ''
        return {
            'tenant': tenant,
            'event': 'invoice',
            'invoice_id': self.id,
            'invoice_name': self.name or '',
            'move_type': self.move_type,
            'document_number': document_number,
            'sale_order_id': order.id,
            'client_order_ref': order.client_order_ref or '',
            'amount_total': float(self.amount_total or 0.0),
            'currency': self.currency_id.name or '',
            'invoice_date': fields.Date.to_string(posted_at),
        }

    def _marketplace_notify_acelio(self):
        """Encola un aviso firmado por cada factura de marketplace publicada."""
        config = self.env['marketplace.push.config']._get_active()
        if not config:
            return False
        sent = 0
        for move in self:
            channel = move._marketplace_channel()
            if not channel:
                continue
            payload = move._marketplace_acelio_payload(config.acelio_tenant)
            if not payload:
                continue
            body = acelio_dump(payload)
            log_vals = {
                'channel': channel,
                'instance_name': config.acelio_tenant,
                'reference': payload['client_order_ref'],
                'move_id': move.id,
            }
            job = partial(
                _push_after_commit,
                self.env.cr.dbname, config.id,
                config.acelio_webhook_url, config.acelio_secret, body, log_vals,
            )
            postcommit = getattr(self.env.cr, 'postcommit', None)
            if postcommit is not None:
                postcommit.add(job)
            else:  # pragma: no cover - Odoo < 16
                job()
            sent += 1
        return sent
