from odoo import models, fields
import logging

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    meli_order_id = fields.Char('MercadoLibre Order ID', readonly=True, index=True)
    meli_instance_id = fields.Many2one('meli.instance', 'ML Account', readonly=True)
    meli_payment_method = fields.Char('Método de Pago (ML)', readonly=True)
    meli_payment_type = fields.Char('Tipo de Pago (ML)', readonly=True)

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
