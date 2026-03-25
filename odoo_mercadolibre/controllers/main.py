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
        """Serve product images to MercadoLibre API."""
        if model not in ['product.template', 'meli.product.image']:
            return werkzeug.exceptions.Forbidden()

        env = request.env
        record = env[model].sudo().browse(id)
        if not record.exists():
            _logger.warning("ML Image: record %s(%s) not found", model, id)
            return werkzeug.exceptions.NotFound()

        image_data = self._read_image_bytes(env, model, id, field, record)
        if not image_data:
            _logger.warning("ML Image: no data for %s(%s).%s", model, id, field)
            return werkzeug.exceptions.NotFound()

        content_type = _detect_image_mime(image_data)
        _logger.info(
            "ML Image: serving %s(%s).%s — %d bytes as %s",
            model, id, field, len(image_data), content_type
        )
        return request.make_response(
            image_data,
            [('Content-Type', content_type),
             ('Content-Length', str(len(image_data)))]
        )

    def _read_image_bytes(self, env, model, id, field, record):
        """
        Read raw image bytes from a Binary/Image field.
        Tries multiple strategies to handle Odoo 16-19 storage differences.
        """
        # Strategy 1: read via ORM with bin_size=False (returns base64 string or bytes)
        try:
            values = record.with_context(bin_size=False).read([field])
            raw = values[0].get(field) if values else None
            if raw:
                data = raw if isinstance(raw, bytes) else base64.b64decode(raw)
                if len(data) > 16:   # sanity check: any real image is > 16 bytes
                    _logger.info("ML Image: strategy 1 (ORM read) — %d bytes", len(data))
                    return data
        except Exception as e:
            _logger.warning("ML Image: strategy 1 failed: %s", e)

        # Strategy 2: read directly from ir.attachment
        try:
            attachment = env['ir.attachment'].sudo().search([
                ('res_model', '=', model),
                ('res_id', '=', id),
                ('res_field', '=', field),
            ], limit=1, order='id desc')
            if attachment and attachment.datas:
                data = base64.b64decode(attachment.datas)
                if len(data) > 16:
                    _logger.info("ML Image: strategy 2 (ir.attachment) — %d bytes", len(data))
                    return data
        except Exception as e:
            _logger.warning("ML Image: strategy 2 failed: %s", e)

        return None
