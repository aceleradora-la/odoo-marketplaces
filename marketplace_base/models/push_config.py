import hashlib
import hmac
import json
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

try:
    import requests
except ImportError:  # pragma: no cover - requests siempre está en Odoo
    requests = None

_logger = logging.getLogger(__name__)

# Prefijo que Acelio Sync escribe en sale_order.client_order_ref
# (src/lib/sync/order-processor.ts → `${CHANNEL_REF[channel]} ${remoteOrderId}`).
# NO cambiar sin cambiarlo del otro lado.
ACELIO_REF_PREFIXES = {
    'ML ': 'meli',
    'TN ': 'tiendanube',
}

PUSH_TIMEOUT = 5  # segundos: la notificación nunca puede colgar una validación


def acelio_sign(secret, body):
    """HMAC-SHA256 hex del cuerpo exacto que se envía."""
    return hmac.new(
        (secret or '').encode('utf-8'), body.encode('utf-8'), hashlib.sha256
    ).hexdigest()


def acelio_dump(payload):
    """Serialización canónica: es la que se firma Y la que se envía."""
    return json.dumps(payload, sort_keys=True, separators=(',', ':'))


def acelio_post(url, secret, body):
    """
    POST firmado a Acelio. Nunca lanza: devuelve (ok, mensaje).
    Se usa tanto inline (botón Probar) como después del commit.
    """
    if requests is None:
        return False, "La librería 'requests' no está disponible en este Odoo"
    try:
        response = requests.post(
            url,
            data=body.encode('utf-8'),
            headers={
                'Content-Type': 'application/json',
                'X-Acelio-Signature': acelio_sign(secret, body),
            },
            timeout=PUSH_TIMEOUT,
        )
        if 200 <= response.status_code < 300:
            return True, "HTTP %s %s" % (response.status_code, (response.text or '')[:300])
        return False, "HTTP %s %s" % (response.status_code, (response.text or '')[:300])
    except Exception as e:  # noqa: BLE001 - cualquier fallo de red es no fatal
        return False, "%s: %s" % (type(e).__name__, e)


class MarketplacePushConfig(models.Model):
    """
    Configuración del aviso en tiempo real hacia Acelio (una fila por compañía).

    Acelio hoy detecta la validación de la entrega por polling. Con esta config
    Odoo le avisa apenas el picking pasa a 'done', y el despacho se notifica al
    marketplace en el momento en vez de esperar al próximo ciclo del scheduler.
    """
    _name = 'marketplace.push.config'
    _description = 'Notificaciones a Acelio'
    _rec_name = 'acelio_tenant'

    company_id = fields.Many2one(
        'res.company', string='Compañía', required=True,
        default=lambda self: self.env.company, ondelete='cascade',
    )
    enabled = fields.Boolean(
        'Activo', default=True,
        help='Si está desactivado, Odoo no envía nada y Acelio sigue detectando por polling.',
    )
    acelio_webhook_url = fields.Char(
        'URL del webhook', help='La copiás de Acelio: Sync → Configuración de la conexión.',
    )
    acelio_tenant = fields.Char(
        'Cuenta en Acelio', help='Identificador (slug) de tu cuenta de Acelio. También lo copiás de ahí.',
    )
    acelio_secret = fields.Char(
        'Secreto', help='Se usa para firmar cada aviso (HMAC-SHA256). Nunca viaja en el pedido.',
    )
    last_push_state = fields.Selection([
        ('success', 'OK'),
        ('error', 'Error'),
    ], string='Último aviso', readonly=True)
    last_push_date = fields.Datetime('Fecha del último aviso', readonly=True)
    last_push_message = fields.Char('Detalle del último aviso', readonly=True)

    @api.constrains('company_id')
    def _check_unique_company(self):
        for rec in self:
            duplicate = self.sudo().search_count([
                ('company_id', '=', rec.company_id.id),
                ('id', '!=', rec.id),
            ])
            if duplicate:
                raise ValidationError(
                    _("Ya existe una configuración de notificaciones para esta compañía.")
                )

    @api.model
    def _get_active(self, company=None):
        """Config utilizable (activa y completa) de la compañía, o vacío."""
        company = company or self.env.company
        domain = [
            ('enabled', '=', True),
            ('acelio_webhook_url', '!=', False),
            ('acelio_secret', '!=', False),
            ('acelio_tenant', '!=', False),
        ]
        config = self.sudo().search(domain + [('company_id', '=', company.id)], limit=1)
        return config or self.sudo().search(domain, limit=1)

    def _register_result(self, ok, message):
        """Deja el resultado del último envío visible en la config."""
        try:
            self.sudo().write({
                'last_push_state': 'success' if ok else 'error',
                'last_push_date': fields.Datetime.now(),
                'last_push_message': (message or '')[:500],
            })
        except Exception:  # noqa: BLE001
            _logger.exception("marketplace.push.config: no se pudo guardar el último resultado")

    def action_test_connection(self):
        """Envía un ping firmado para verificar URL, cuenta y secreto."""
        self.ensure_one()
        if not (self.acelio_webhook_url and self.acelio_secret and self.acelio_tenant):
            raise UserError(_("Completá la URL, la cuenta y el secreto antes de probar."))
        body = acelio_dump({'tenant': self.acelio_tenant, 'ping': True})
        ok, message = acelio_post(self.acelio_webhook_url, self.acelio_secret, body)
        self._register_result(ok, message)
        self.env['marketplace.sync.log'].log_event(
            'other', 'other', 'success' if ok else 'error',
            instance_name=self.acelio_tenant, reference='ping',
            message=_("Prueba de notificación a Acelio: %s") % message,
        )
        if not ok:
            raise UserError(_("Acelio no aceptó la prueba.\n\n%s") % message)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Conexión OK"),
                'message': _("Acelio recibió la prueba correctamente."),
                'type': 'success',
                'sticky': False,
            },
        }
