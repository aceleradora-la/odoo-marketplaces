from odoo import models, fields, _
from odoo.exceptions import UserError
import base64
import logging

_logger = logging.getLogger(__name__)

_SHIPMENT_STATUS_LABELS = {
    'pending': 'Pendiente',
    'handling': 'En preparación',
    'ready_to_ship': 'Listo para enviar',
    'shipped': 'Enviado',
    'delivered': 'Entregado',
    'not_delivered': 'No entregado',
    'cancelled': 'Cancelado',
}


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    meli_order_id = fields.Char('MercadoLibre Order ID', readonly=True, index=True)
    meli_instance_id = fields.Many2one('meli.instance', 'ML Account', readonly=True)
    meli_payment_method = fields.Char('Método de Pago (ML)', readonly=True)
    meli_payment_type = fields.Char('Tipo de Pago (ML)', readonly=True)

    meli_sale_fee = fields.Float('Comisión ML', readonly=True,
                                 help='Comisión cobrada por MercadoLibre por esta venta')
    meli_shipping_cost = fields.Float('Costo de Envío (vendedor)', readonly=True,
                                      help='Parte del costo de envío a cargo del vendedor')

    meli_shipment_id = fields.Char('ML Shipment ID', readonly=True, index=True)
    meli_shipment_status = fields.Char('Estado del Envío (ML)', readonly=True)
    meli_tracking_number = fields.Char('Tracking (ML)', readonly=True)
    meli_logistic_type = fields.Char('Tipo de Logística (ML)', readonly=True)

    def action_meli_shipment_status(self):
        """Fetch current shipment status and tracking number from ML."""
        for order in self:
            if not order.meli_shipment_id or not order.meli_instance_id:
                continue
            resp = order.meli_instance_id._call_api(
                'GET', f"https://api.mercadolibre.com/shipments/{order.meli_shipment_id}"
            )
            if resp.status_code != 200:
                raise UserError(_("Error consultando el envío en ML: %s") % resp.text)

            data = resp.json()
            status = data.get('status', '')
            order.write({
                'meli_shipment_status': _SHIPMENT_STATUS_LABELS.get(status, status),
                'meli_tracking_number': data.get('tracking_number') or '',
                'meli_logistic_type': data.get('logistic_type') or order.meli_logistic_type,
            })
            order.message_post(
                body=f"Envío ML actualizado: {order.meli_shipment_status}"
                     + (f" — Tracking: {order.meli_tracking_number}" if order.meli_tracking_number else "")
            )
        return True

    def action_meli_download_label(self):
        """Download the shipping label PDF from ML and attach it to the order."""
        self.ensure_one()
        if not self.meli_shipment_id or not self.meli_instance_id:
            raise UserError(_("Este pedido no tiene un envío de MercadoLibre asociado."))

        resp = self.meli_instance_id._call_api(
            'GET', "https://api.mercadolibre.com/shipment_labels",
            params={'shipment_ids': self.meli_shipment_id, 'response_type': 'pdf'},
        )
        if resp.status_code != 200:
            raise UserError(
                _("Error descargando la etiqueta (la etiqueta solo está disponible "
                  "cuando el envío está listo para despachar): %s") % resp.text
            )

        attachment = self.env['ir.attachment'].create({
            'name': f"Etiqueta_ML_{self.meli_shipment_id}.pdf",
            'type': 'binary',
            'datas': base64.b64encode(resp.content),
            'res_model': 'sale.order',
            'res_id': self.id,
            'mimetype': 'application/pdf',
        })
        self.message_post(
            body=_("Etiqueta de envío descargada de MercadoLibre."),
            attachment_ids=[attachment.id],
        )
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }

    def action_sync_messages(self):
        """Fetch post-sale messages from MercadoLibre and post them to chatter."""
        for order in self:
            if not order.meli_order_id or not order.meli_instance_id:
                continue

            instance = order.meli_instance_id
            if instance.state != 'authenticated':
                continue

            # Use cached seller_id or fetch it via _call_api
            seller_id = instance._ensure_seller_id()
            if not seller_id:
                _logger.error("Could not determine seller_id for instance %s", instance.name)
                continue

            pack_id = order.meli_order_id
            url = (
                f"https://api.mercadolibre.com/messages/packs/{pack_id}"
                f"/sellers/{seller_id}?tag=post_sale"
            )

            response = instance._call_api('GET', url)
            if response.status_code != 200:
                _logger.error(
                    "Error fetching ML messages for order %s: %s",
                    order.name, response.text
                )
                continue

            for msg in response.json().get('messages', []):
                msg_id = msg.get('id', '')
                text = msg.get('text', '')
                sender_role = msg.get('from', {}).get('role', '')

                # Deduplicate by hidden msg_id in body
                existing = self.env['mail.message'].search([
                    ('res_id', '=', order.id),
                    ('model', '=', 'sale.order'),
                    ('body', 'ilike', msg_id),
                ], limit=1)

                if not existing:
                    prefix = "ML Comprador:" if sender_role == 'buyer' else "ML Vendedor:"
                    body = (
                        f"<strong>{prefix}</strong> {text}"
                        f"<span style='display:none'>{msg_id}</span>"
                    )
                    order.message_post(body=body)
