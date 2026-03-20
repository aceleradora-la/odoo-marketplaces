from odoo import http
from odoo.http import request

class MeliImageController(http.Controller):
    
    @http.route([
        '/meli_image/<model>/<int:res_id>/<field>',
    ], type='http', auth='public', website=False)
    def meli_image(self, model, res_id, field, **kwargs):
        """Public endpoint to serve images to MercadoLibre"""
        if model not in ['product.template', 'meli.product.image']:
            return request.not_found()
            
        # Get record and check if it exists
        record = request.env[model].sudo().browse(res_id)
        if not record.exists():
            return request.not_found()
            
        # Serve image
        status, headers, content = request.env['ir.http'].sudo().binary_content(
            model=model, id=res_id, field=field, default_mimetype='image/png'
        )
        if status == 304:
            return http.Response(status=304)
        elif status == 404:
            return request.not_found()
            
        return request.make_response(content, headers)
