from odoo import http
from odoo.http import request
import werkzeug
import base64
import logging

_logger = logging.getLogger(__name__)


def _detect_image_mime(image_data):
    """Detect image MIME type from magic bytes."""
    if image_data[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if image_data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if image_data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if image_data[:4] == b'RIFF' and image_data[8:12] == b'WEBP':
        return 'image/webp'
    return 'image/jpeg'  # safe fallback


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
        """Serve product images to MercadoLibre API with correct Content-Type."""
        if model not in ['product.template', 'meli.product.image']:
            return werkzeug.exceptions.Forbidden()

        record = request.env[model].sudo().browse(id)
        if not record.exists():
            _logger.warning("ML Image Request: Record %s(%s) not found", model, id)
            return werkzeug.exceptions.NotFound()

        image_base64 = record[field]
        if not image_base64:
            _logger.warning("ML Image Request: Field %s in %s(%s) is empty", field, model, id)
            return werkzeug.exceptions.NotFound()

        try:
            image_data = base64.b64decode(image_base64)
            content_type = _detect_image_mime(image_data)
            return request.make_response(image_data, [('Content-Type', content_type)])
        except Exception as e:
            _logger.error("Error serving ML image for %s(%s): %s", model, id, str(e))
            return werkzeug.exceptions.NotFound()
