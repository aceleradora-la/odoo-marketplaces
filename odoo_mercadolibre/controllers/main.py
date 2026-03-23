from odoo import http
from odoo.http import request
import werkzeug

class MeliController(http.Controller):

    @http.route('/meli/auth', type='http', auth="public")
    def meli_auth(self, **kwargs):
        code = kwargs.get('code')
        if code:
            html = f"""
                <html>
                    <body style="font-family: sans-serif; text-align: center; padding: 50px;">
                        <h1 style="color: #2e7d32;">¡Autenticación Exitosa!</h1>
                        <p>Por favor, copia el siguiente código y pégalo en Odoo:</p>
                        <div style="background: #f5f5f5; padding: 20px; border: 2px dashed #ccc; font-size: 24px; margin: 20px 0; word-break: break-all;">
                            <code>{code}</code>
                        </div>
                        <button onclick="navigator.clipboard.writeText('{code}'); alert('Código copiado');" 
                                style="padding: 10px 20px; background: #ffdb00; border: none; cursor: pointer; border-radius: 5px; font-weight: bold;">
                            Copiar Código
                        </button>
                    </body>
                </html>
            """
            return request.make_response(html, [('Content-Type', 'text/html')])
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
