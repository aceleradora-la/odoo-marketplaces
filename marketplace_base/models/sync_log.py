from odoo import models, fields, api, _
from odoo.exceptions import UserError
import json
import logging

_logger = logging.getLogger(__name__)


class MarketplaceSyncLog(models.Model):
    """
    Shared synchronization log for marketplace connectors (MercadoLibre, TiendaNube).
    Every webhook, cron or API operation worth auditing gets a record here, so
    errors are visible from the Odoo UI instead of only the server log.
    """
    _name = 'marketplace.sync.log'
    _description = 'Marketplace Sync Log'
    _order = 'create_date desc'

    name = fields.Char(compute='_compute_name', store=True)
    channel = fields.Selection([
        ('meli', 'MercadoLibre'),
        ('tiendanube', 'TiendaNube'),
        ('other', 'Otro'),
    ], required=True, index=True)
    instance_name = fields.Char('Cuenta / Tienda', index=True)
    operation = fields.Selection([
        ('order_webhook', 'Pedido (webhook)'),
        ('order_sync', 'Pedido (polling)'),
        ('payment', 'Registro de pago'),
        ('stock_sync', 'Sync stock/precio'),
        ('publish', 'Publicación'),
        ('fulfillment', 'Notificación de envío'),
        ('import', 'Importación'),
        ('other', 'Otro'),
    ], required=True, default='other', index=True)
    reference = fields.Char('Referencia externa', index=True,
                            help='ID del pedido/ítem en el marketplace')
    state = fields.Selection([
        ('success', 'OK'),
        ('error', 'Error'),
        ('retried', 'Reintentado'),
    ], required=True, default='success', index=True)
    message = fields.Text('Detalle')
    payload = fields.Text('Payload (JSON)',
                          help='Datos originales recibidos; usados por el botón Reintentar')
    res_model = fields.Char('Modelo relacionado')
    res_id = fields.Integer('ID relacionado')

    @api.depends('channel', 'operation', 'reference')
    def _compute_name(self):
        for rec in self:
            rec.name = f"[{rec.channel or ''}] {rec.operation or ''} {rec.reference or ''}"

    # ------------------------------------------------------------------
    # API used by the connector modules
    # ------------------------------------------------------------------

    @api.model
    def log_event(self, channel, operation, state, instance_name='', reference='',
                  message='', payload=None, res_model='', res_id=0):
        """Create a log entry. Never raises — logging must not break the flow."""
        try:
            vals = {
                'channel': channel,
                'operation': operation,
                'state': state,
                'instance_name': instance_name,
                'reference': str(reference or ''),
                'message': message or '',
                'res_model': res_model,
                'res_id': res_id,
            }
            if payload is not None:
                vals['payload'] = (
                    payload if isinstance(payload, str) else json.dumps(payload, default=str)
                )
            return self.sudo().create(vals)
        except Exception as e:
            _logger.error("marketplace.sync.log: could not create log entry: %s", e)
            return self.browse()

    # ------------------------------------------------------------------
    # UI actions
    # ------------------------------------------------------------------

    def action_open_record(self):
        self.ensure_one()
        if not self.res_model or not self.res_id:
            raise UserError(_("Este registro no tiene documento relacionado."))
        return {
            'type': 'ir.actions.act_window',
            'res_model': self.res_model,
            'res_id': self.res_id,
            'view_mode': 'form',
        }

    def action_retry(self):
        """
        Re-dispatch the stored payload. Only order operations are retryable:
        the payload is the full marketplace order dict and is re-processed
        through the instance's _process_single_order.
        """
        for rec in self:
            if rec.operation not in ('order_webhook', 'order_sync'):
                raise UserError(_("Solo se pueden reintentar operaciones de pedidos."))
            if not rec.payload:
                raise UserError(_("El registro no tiene payload guardado para reintentar."))

            instance_model = {
                'meli': 'meli.instance',
                'tiendanube': 'tn.instance',
            }.get(rec.channel)
            if not instance_model or instance_model not in self.env:
                raise UserError(_("El módulo del canal '%s' no está instalado.") % rec.channel)

            instance = self.env[instance_model].search([
                ('name', '=', rec.instance_name),
                ('state', '=', 'authenticated'),
            ], limit=1)
            if not instance:
                raise UserError(
                    _("No se encontró una cuenta autenticada llamada '%s'.") % rec.instance_name
                )

            order_data = json.loads(rec.payload)
            instance._process_single_order(order_data)
            rec.write({'state': 'retried'})
        return True

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------

    @api.model
    def cron_cleanup(self, days=30):
        """Delete successful log entries older than `days`."""
        limit = fields.Datetime.subtract(fields.Datetime.now(), days=days)
        old = self.search([('state', '=', 'success'), ('create_date', '<', limit)])
        count = len(old)
        old.unlink()
        _logger.info("marketplace.sync.log: cleaned %s old success entries", count)
