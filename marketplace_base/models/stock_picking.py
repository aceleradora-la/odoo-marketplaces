import logging
from functools import partial

from odoo import models, fields, api, SUPERUSER_ID
from odoo.modules.registry import Registry

from .push_config import ACELIO_REF_PREFIXES, acelio_dump, acelio_post

_logger = logging.getLogger(__name__)


def _push_after_commit(dbname, config_id, url, secret, body, log_vals):
    """
    Corre DESPUÉS del commit del picking: el POST no bloquea la validación y,
    si Acelio está caído, la entrega ya quedó confirmada igual.
    Abre su propio cursor porque el de la transacción original ya está cerrado.
    """
    ok, message = acelio_post(url, secret, body)
    try:
        with Registry(dbname).cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            env['marketplace.sync.log'].log_event(
                log_vals['channel'], 'fulfillment', 'success' if ok else 'error',
                instance_name=log_vals['instance_name'],
                reference=log_vals['reference'],
                message=("Aviso de entrega a Acelio: %s" % message)[:2000],
                payload=body,
                res_model='stock.picking',
                res_id=log_vals['picking_id'],
            )
            env['marketplace.push.config'].browse(config_id)._register_result(ok, message)
    except Exception:  # noqa: BLE001
        _logger.exception("marketplace_base: no se pudo registrar el aviso a Acelio")


class StockPicking(models.Model):
    """
    Aviso a Acelio cuando se valida una entrega de un pedido de marketplace.

    Se engancha en `_action_done` y no en `write`/`button_validate`: `state` es
    un campo calculado almacenado (se escribe por el ORM, no por `write`) y
    `button_validate` puede devolver un wizard (backorder / cantidades) sin
    haber terminado. TODOS los caminos que dejan un picking en 'done' pasan por
    `_action_done`, así que es el único punto confiable.
    """
    _inherit = 'stock.picking'

    def _action_done(self):
        res = super()._action_done()
        try:
            self._marketplace_notify_acelio()
        except Exception:  # noqa: BLE001
            # Nunca romper la validación de la entrega por una notificación.
            _logger.exception("marketplace_base: fallo al preparar el aviso a Acelio")
        return res

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _marketplace_sale_order(self):
        """Pedido de venta del picking (sale_id lo agrega sale_stock)."""
        self.ensure_one()
        if 'sale_id' in self._fields and self.sale_id:
            return self.sale_id
        group = self.group_id
        if group and 'sale_id' in group._fields:
            return group.sale_id
        return self.env['sale.order'].browse()

    def _marketplace_channel(self):
        """Canal deducido del prefijo de client_order_ref, o False si no aplica."""
        self.ensure_one()
        if self.state != 'done' or self.picking_type_code != 'outgoing':
            return False
        order = self._marketplace_sale_order()
        ref = order.client_order_ref or '' if order else ''
        for prefix, channel in ACELIO_REF_PREFIXES.items():
            if ref.startswith(prefix):
                return channel
        return False

    def _marketplace_acelio_payload(self, tenant):
        """Payload del aviso, o False si el picking no es de un marketplace."""
        self.ensure_one()
        if not self._marketplace_channel():
            return False
        order = self._marketplace_sale_order()
        validated_at = self.date_done or fields.Datetime.now()
        tracking = ''
        if 'carrier_tracking_ref' in self._fields:
            tracking = self.carrier_tracking_ref or ''
        return {
            'tenant': tenant,
            'picking_id': self.id,
            'picking_name': self.name or '',
            'sale_order_id': order.id,
            'client_order_ref': order.client_order_ref or '',
            'tracking_ref': tracking,
            'validated_at': fields.Datetime.to_string(validated_at).replace(' ', 'T') + 'Z',
        }

    def _marketplace_notify_acelio(self):
        """Encola un aviso firmado por cada picking de marketplace recién hecho."""
        config = self.env['marketplace.push.config']._get_active()
        if not config:
            return False
        sent = 0
        for picking in self:
            channel = picking._marketplace_channel()
            if not channel:
                continue
            payload = picking._marketplace_acelio_payload(config.acelio_tenant)
            if not payload:
                continue
            body = acelio_dump(payload)
            log_vals = {
                'channel': channel,
                'instance_name': config.acelio_tenant,
                'reference': payload['client_order_ref'],
                'picking_id': picking.id,
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
