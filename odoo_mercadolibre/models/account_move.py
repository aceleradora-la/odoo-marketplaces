from odoo import models, fields, api
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

            pdf_content, _ = self.env['ir.actions.report']._render_qweb_pdf(
                'account.account_invoices', move.id
            )

            url = f"https://api.mercadolibre.com/packs/{move.meli_order_id}/fiscal_documents"
            files = {
                'fiscal_document': (f'Factura_{move.name}.pdf', pdf_content, 'application/pdf')
            }

            try:
                response = move.meli_instance_id._call_api('POST', url, files=files)
                if response.status_code in [200, 201]:
                    move.meli_invoice_uploaded = True
                    _logger.info("Invoice %s uploaded successfully to MercadoLibre", move.name)
                else:
                    _logger.error(
                        "Failed to upload invoice %s: %s - %s",
                        move.name, response.status_code, response.text
                    )
            except Exception as e:
                _logger.error("Exception uploading invoice %s: %s", move.name, str(e))

    @api.model
    def cron_upload_meli_invoices(self):
        moves = self.search([
            ('state', '=', 'posted'),
            ('meli_order_id', '!=', False),
            ('meli_invoice_uploaded', '=', False),
        ])
        moves.action_upload_meli_invoice()
