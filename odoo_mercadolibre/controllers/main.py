from odoo import http
from odoo.http import request
import werkzeug

class MeliController(http.Controller):

    @http.route('/meli/auth', type='http', auth="public", website=True)
    def meli_auth(self, **kwargs):
        code = kwargs.get('code')
        if code:
            return request.render('odoo_mercadolibre.meli_auth_success', {
                'code': code,
            })
        return "No code received"

    @http.route([
        '/meli_image/<model>/<int:id>/<field>',
        '/meli_image/<model>/<int:id>/<field>/<string:filename>'
    ], type='http', auth="public")
    def meli_image(self, model, id, field, **kwargs):
        # Serve image from Odoo to ML (Publicly accessible but restricted to specific models)
        if model not in ['product.template', 'meli.product.image']:
            return werkzeug.exceptions.Forbidden()
        
        record = request.env[model].sudo().browse(id)
        if not record.exists():
             return werkzeug.exceptions.NotFound()
             
        status, headers, content = request.env['ir.http'].sudo().binary_content(
            model=model, id=id, field=field, default_mimetype='image/png')
            
        if status != 200:
            return werkzeug.exceptions.NotFound()
            
        return request.make_response(content, headers)
