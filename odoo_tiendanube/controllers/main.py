from odoo import http
from odoo.http import request
import json
import logging

_logger = logging.getLogger(__name__)


class TiendaNubeController(http.Controller):

    @http.route('/tiendanube/auth', type='http', auth='public')
    def tn_auth(self, **kwargs):
        code = kwargs.get('code')
        if code:
            html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>TiendaNube Auth</title></head>
<body style="font-family:sans-serif;text-align:center;padding:50px">
  <h1 style="color:#3c3ce6">¡Autenticación Exitosa!</h1>
  <p>Copiá el siguiente código y pegalo en Odoo:</p>
  <div style="background:#f5f5f5;padding:20px;border:2px dashed #ccc;font-size:22px;margin:20px 0;word-break:break-all">
    <code id="tn_code">{code}</code>
  </div>
  <button onclick="navigator.clipboard.writeText(document.getElementById('tn_code').innerText);alert('Código copiado')"
          style="padding:10px 20px;background:#3c3ce6;color:#fff;border:none;cursor:pointer;border-radius:5px;font-weight:bold">
    Copiar Código
  </button>
</body>
</html>"""
            return request.make_response(html, [('Content-Type', 'text/html; charset=utf-8')])
        return request.make_response('No code received', [('Content-Type', 'text/plain')])

    @http.route('/tiendanube/webhook', type='http', auth='public', methods=['POST'], csrf=False)
    def tn_webhook(self, **kwargs):
        """
        Receives real-time notifications from TiendaNube.
        Payload: {"store_id": 12345, "event": "orders/paid", "id": 9876}
        TN expects a 200 response quickly.
        """
        try:
            payload = json.loads(request.httprequest.data or '{}')
        except Exception:
            payload = {}

        store_id = str(payload.get('store_id', ''))
        event = payload.get('event', '')
        resource_id = str(payload.get('id', ''))

        _logger.info("TN Webhook: event=%s store_id=%s id=%s", event, store_id, resource_id)

        if not store_id or not resource_id:
            return request.make_response('{}', [('Content-Type', 'application/json')])

        try:
            instance = request.env['tn.instance'].sudo().search(
                [('tn_store_id', '=', store_id), ('state', '=', 'authenticated')], limit=1
            )
            if not instance:
                _logger.warning("TN Webhook: no authenticated instance for store_id=%s", store_id)
                request.env['marketplace.sync.log'].sudo().log_event(
                    'tiendanube', 'order_webhook', 'error', reference=resource_id,
                    message=f"Webhook recibido pero no hay tienda autenticada con store_id={store_id}",
                    payload=payload,
                )
                return request.make_response('{}', [('Content-Type', 'application/json')])

            if event in ('order/paid', 'order/created'):
                resp = instance._call_api('GET', f'/orders/{resource_id}')
                if resp.status_code == 200:
                    instance.sudo()._process_single_order(resp.json())
                else:
                    _logger.error("TN Webhook: error fetching order %s — %s", resource_id, resp.text)
                    instance.sudo()._mkt_log(
                        'order_webhook', 'error', reference=resource_id,
                        message=f"Error consultando el pedido en TN ({resp.status_code}): {resp.text[:500]}",
                        payload=payload,
                    )

            elif event == 'order/cancelled':
                self._handle_order_cancelled(instance, resource_id)

            elif event in ('product/updated', 'product/created'):
                self._handle_product_updated(instance, resource_id)

            elif event == 'product/deleted':
                self._handle_product_deleted(instance, resource_id)

        except Exception as e:
            _logger.error("TN Webhook exception (event=%s id=%s): %s", event, resource_id, str(e))
            request.env['marketplace.sync.log'].sudo().log_event(
                'tiendanube', 'order_webhook', 'error', reference=resource_id,
                message=f"Excepción procesando webhook {event}: {e}", payload=payload,
            )

        return request.make_response('{}', [('Content-Type', 'application/json')])

    def _handle_order_cancelled(self, instance, tn_order_id):
        so = request.env['sale.order'].sudo().search(
            [('tn_order_id', '=', tn_order_id)], limit=1
        )
        if so and so.state not in ('cancel', 'done'):
            so.with_context(disable_cancel_warning=True).action_cancel()
            so.message_post(body="Pedido cancelado desde TiendaNube.")
            _logger.info("TN Webhook: cancelled SO %s (TN order %s)", so.name, tn_order_id)

    def _handle_product_updated(self, instance, tn_product_id):
        """When TN notifies a product change, re-sync price/stock back to TN (bidirectional safety)."""
        tn_product = request.env['tn.product'].sudo().search([
            ('tn_product_id', '=', tn_product_id),
            ('instance_id', '=', instance.id),
        ], limit=1)
        if tn_product:
            _logger.info("TN Webhook: product %s updated externally — no action (Odoo is master)", tn_product_id)

    def _handle_product_deleted(self, instance, tn_product_id):
        tn_product = request.env['tn.product'].sudo().search([
            ('tn_product_id', '=', tn_product_id),
            ('instance_id', '=', instance.id),
        ], limit=1)
        if tn_product:
            tn_product.write({'status': 'draft', 'tn_product_id': False})
            tn_product.message_post(body="Producto eliminado en TiendaNube (notificación webhook).")
            _logger.info("TN Webhook: product %s marked as draft in Odoo", tn_product_id)

    @http.route([
        '/tn_image/<model>/<int:record_id>/<field>',
    ], type='http', auth='public')
    def tn_image(self, model, record_id, field, **kwargs):
        """Serve product images to TiendaNube (used in product payloads)."""
        import werkzeug
        import base64

        if model not in ('product.template', 'product.product'):
            return werkzeug.exceptions.Forbidden()

        record = request.env[model].sudo().browse(record_id)
        if not record.exists():
            return werkzeug.exceptions.NotFound()

        try:
            values = record.with_context(bin_size=False).read([field])
            raw = values[0].get(field) if values else None
            if raw:
                data = raw if isinstance(raw, bytes) else base64.b64decode(raw)
                if len(data) > 16:
                    return request.make_response(
                        data,
                        [('Content-Type', 'image/jpeg'),
                         ('Content-Length', str(len(data)))]
                    )
        except Exception as e:
            _logger.warning("TN image error for %s(%s).%s: %s", model, record_id, field, e)

        return werkzeug.exceptions.NotFound()
