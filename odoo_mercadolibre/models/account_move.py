from odoo import models, fields, api, _
import requests
import base64
import logging

_logger = logging.getLogger(__name__)

class AccountMove(models.Model):
    _inherit = 'account.move'

    meli_instance_id = fields.Many2one('meli.instance', 'Meli Account')
    meli_order_id = fields.Char('MercadoLibre Order ID')
    meli_invoice_uploaded = fields.Boolean('Uploaded to Meli', default=False)

    def action_upload_meli_invoice(self):
        for move in self:
            if not move.meli_instance_id or not move.meli_order_id or move.state != 'posted':
                continue
                
            if move.meli_invoice_uploaded:
                continue

            # API to upload invoice PDF
            # POST /packs/{pack_id}/fiscal_documents requires the pack_id (which is usually the order_id)
            # We assume order_id here.
            
            # Generate PDF
            pdf_content, _ = self.env['ir.actions.report']._render_qweb_pdf('account.account_invoices', move.id)
            
            url = f"https://api.mercadolibre.com/packs/{move.meli_order_id}/fiscal_documents"
            headers = {
                'Authorization': f'Bearer {move.meli_instance_id.access_token}'
            }
            files = {
                'fiscal_document': (f'Factura_{move.name}.pdf', pdf_content, 'application/pdf')
            }
            
            try:
                response = requests.post(url, headers=headers, files=files)
                if response.status_code in [200, 201]:
                    move.meli_invoice_uploaded = True
                    _logger.info(f"Successfully uploaded invoice for move {move.name} to MercadoLibre")
                else:
                    _logger.error(f"Failed to upload invoice {move.name} to MercadoLibre. Status Code: {response.status_code}. Response: {response.text}")
            except Exception as e:
                _logger.error(f"Exception while uploading invoice {move.name} to MercadoLibre: {str(e)}")

    @api.model
    def cron_upload_meli_invoices(self):
        moves = self.search([
            ('state', '=', 'posted'),
            ('meli_order_id', '!=', False),
            ('meli_invoice_uploaded', '=', False)
        ])
        moves.action_upload_meli_invoice()
