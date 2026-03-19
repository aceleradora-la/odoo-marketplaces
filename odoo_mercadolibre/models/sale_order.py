from odoo import models, fields, api, _
from odoo.exceptions import UserError
import requests
import logging

_logger = logging.getLogger(__name__)

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    meli_order_id = fields.Char('MercadoLibre Order ID', readonly=True, index=True)
    meli_instance_id = fields.Many2one('meli.instance', 'ML Account', readonly=True)
    
    def action_sync_messages(self):
        """ Fetch messages from MercadoLibre and post them to chatter """
        for order in self:
            if not order.meli_order_id or not order.meli_instance_id:
                continue
                
            instance = order.meli_instance_id
            if instance.state != 'authenticated':
                continue
                
            # Need seller_id for the endpoint: /messages/packs/{pack_id}/sellers/{seller_id}
            # We can get seller_id from the instance if we store it, or fetch it.
            # For simplicity, we get it from /users/me
            headers = {'Authorization': f'Bearer {instance.access_token}'}
            user_response = requests.get("https://api.mercadolibre.com/users/me", headers=headers)
            if user_response.status_code != 200:
                _logger.error(f"Error fetching seller info for messages: {user_response.text}")
                continue
                
            seller_id = user_response.json().get('id')
            pack_id = order.meli_order_id # typically the order ID is the pack ID
            
            url = f"https://api.mercadolibre.com/messages/packs/{pack_id}/sellers/{seller_id}?tag=post_sale"
            
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                messages = response.json().get('messages', [])
                for msg in messages:
                    # check if the message is already in chatter? Odoo doesn't easily let us find by external ID on mail.message unless we extend it.
                    # As a simple approach, we can track the last message sync date, but ML messages don't always have simple sequential IDs.
                    # For now, we just log a debug or we'd need a way to deduplicate.
                    msg_id = msg.get('id')
                    text = msg.get('text')
                    sender_role = msg.get('from', {}).get('role') # 'seller' or 'buyer'
                    
                    # Deduplication check: search existing mail.message
                    existing = self.env['mail.message'].search([
                        ('res_id', '=', order.id),
                        ('model', '=', 'sale.order'),
                        ('body', 'ilike', msg_id)
                    ], limit=1)
                    
                    if not existing:
                        prefix = "ML Buyer:" if sender_role == 'buyer' else "ML Seller:"
                        body = f"<strong>{prefix}</strong> {text} <span style='display:none'>{msg_id}</span>"
                        order.message_post(body=body)
            else:
                _logger.error(f"Error fetching ML messages for order {order.name}: {response.text}")
