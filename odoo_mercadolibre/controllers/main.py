import base64
import logging

_logger = logging.getLogger(__name__)

class MeliController(http.Controller):

    @http.route('/meli/auth', type='http', auth="public")
    def meli_auth(self, **kwargs):
        code = kwargs.get('code')
        if code:
            html = f"""
                <!DOCTYPE html>
                <html>
                    <head>
                        <meta charset="UTF-8">
                        <title>Autenticación MercadoLibre</title>
                    </head>
                    <body style="font-family: sans-serif; text-align: center; padding: 50px;">
                        <h1 style="color: #2e7d32;">¡Autenticación Exitosa!</h1>
                        <p>Por favor, copia el siguiente código y pégalo en Odoo:</p>
                        <div style="background: #f5f5f5; padding: 20px; border: 2px dashed #ccc; font-size: 24px; margin: 20px 0; word-break: break-all;">
                            <code id="meli_code_copy">{code}</code>
                        </div>
                        <button onclick="navigator.clipboard.writeText(document.getElementById('meli_code_copy').innerText); alert('Código copiado');" 
                                style="padding: 10px 20px; background: #ffdb00; border: none; cursor: pointer; border-radius: 5px; font-weight: bold;">
                            Copiar Código
                        </button>
                    </body>
                </html>
            """
            return request.make_response(html, [('Content-Type', 'text/html; charset=utf-8')])
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
             _logger.warning(f"ML Image Request: Record {model}({id}) not found")
             return werkzeug.exceptions.NotFound()
             
        image_base64 = record[field]
        if not image_base64:
             _logger.warning(f"ML Image Request: Field {field} in {model}({id}) is empty")
             return werkzeug.exceptions.NotFound()
        
        try:
            image_data = base64.b64decode(image_base64)
            return request.make_response(image_data, [('Content-Type', 'image/png')])
        except Exception as e:
            _logger.error(f"Error serving ML image for {model}({id}): {str(e)}")
            return werkzeug.exceptions.NotFound()
