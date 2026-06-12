from odoo import models, _
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def button_validate(self):
        res = super().button_validate()
        for picking in self:
            if (
                picking.state == 'done'
                and picking.picking_type_code == 'outgoing'
                and picking.sale_id
                and picking.sale_id.tn_order_id
                and picking.sale_id.tn_instance_id
                and picking.sale_id.tn_instance_id.notify_fulfillment
            ):
                picking.sale_id.tn_instance_id._notify_fulfillment(picking)
        return res

    def action_tn_notify_fulfillment(self):
        """Manual button: re-send fulfillment notification to TiendaNube."""
        self.ensure_one()
        so = self.sale_id
        if not so or not so.tn_order_id or not so.tn_instance_id:
            raise UserError(_("Este albarán no está vinculado a un pedido de TiendaNube."))
        ok = so.tn_instance_id._notify_fulfillment(self)
        if not ok:
            raise UserError(_(
                "No se pudo notificar el envío a TiendaNube. "
                "Revisá el Log de Sincronización para más detalle."
            ))
        return True
