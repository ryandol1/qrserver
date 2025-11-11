import base64
import os
import re
from io import BytesIO
from typing import Dict, Tuple, TypedDict

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template_string,
    request,
    send_file,
)
import qrcode


class RedirectEntry(TypedDict):
    final_url: str
    redirect_slug: str


app = Flask(__name__)

# Simple in-memory store mapping external unique IDs to redirect data.
redirect_map: Dict[str, RedirectEntry] = {}


def _sanitize_unique_id(raw_id: str) -> str:
    """
    Convert the provided unique identifier into a URL-safe slug.
    Keeps alphanumeric characters and dashes/underscores, replaces others with dashes.
    """
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw_id.strip())
    return cleaned or "link"


def _build_redirect_url(slug: str) -> str:
    """
    Build a fully qualified redirect URL.
    Priority:
      1. BASE_URL environment variable (expected when running on Render)
      2. Request host URL (useful for local development)
    """
    env_base = os.getenv("BASE_URL")
    if env_base:
        base = env_base.rstrip("/")
    else:
        base = request.url_root.rstrip("/")
    return f"{base}/{slug}"


def _generate_qr_png(data: str) -> BytesIO:
    qr_image = qrcode.make(data)
    buffer = BytesIO()
    qr_image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def _encode_qr_code(data: str) -> str:
    """Generate a PNG QR code for the provided data and return it as a base64 string."""
    buffer = _generate_qr_png(data)
    return base64.b64encode(buffer.read()).decode("ascii")


def _ensure_unique_slug(candidate: str) -> str:
    existing_slugs = {entry["redirect_slug"] for entry in redirect_map.values()}
    redirect_slug = candidate
    original_slug = redirect_slug
    suffix = 1
    while redirect_slug in existing_slugs:
        redirect_slug = f"{original_slug}-{suffix}"
        suffix += 1
    return redirect_slug


def _register_redirect(unique_id: str, final_url: str) -> Tuple[RedirectEntry, str, str]:
    unique_id = str(unique_id).strip()
    if not unique_id:
        raise ValueError("unique_id is required")
    if not final_url:
        raise ValueError("final_url is required")
    if not isinstance(final_url, str):
        raise TypeError("final_url must be a string")

    existing_entry = redirect_map.get(unique_id)

    if existing_entry:
        existing_entry["final_url"] = final_url
        status = "updated"
        redirect_slug = existing_entry["redirect_slug"]
    else:
        redirect_slug = _ensure_unique_slug(_sanitize_unique_id(unique_id))
        redirect_map[unique_id] = {
            "final_url": final_url,
            "redirect_slug": redirect_slug,
        }
        status = "created"

    redirect_url = _build_redirect_url(redirect_slug)
    return redirect_map[unique_id], redirect_url, status


@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok"}), 200


def _resolve_redirect(slug: str):
    for entry in redirect_map.values():
        if entry["redirect_slug"] == slug:
            return redirect(entry["final_url"], code=302)
    return jsonify({"error": "redirect_not_found"}), 404


@app.route("/<slug>", methods=["GET"])
def follow_redirect_root(slug: str):
    # Health and webhook routes take precedence because Flask matches
    # explicit routes before parameterized ones.
    return _resolve_redirect(slug)


@app.route("/redirect/<slug>", methods=["GET"])
def follow_redirect(slug: str):
    return _resolve_redirect(slug)


@app.route("/webhook", methods=["POST"])
def handle_webhook():
    payload = request.get_json(silent=True) or {}

    unique_id = str(payload.get("unique_id", "")).strip()
    final_url = payload.get("final_url")

    if not unique_id:
        return jsonify({"error": "unique_id is required"}), 400

    if not final_url:
        return jsonify({"error": "final_url is required"}), 400

    if not isinstance(final_url, str):
        return jsonify({"error": "final_url must be a string"}), 400

    try:
        entry, redirect_url, status = _register_redirect(unique_id, final_url)
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400

    qr_code_b64 = _encode_qr_code(redirect_url)

    return (
        jsonify(
            {
                "unique_id": unique_id,
                "redirect_url": redirect_url,
                "final_url": entry["final_url"],
                "qr_code_base64": qr_code_b64,
                "status": status,
            }
        ),
        201 if status == "created" else 200,
    )


@app.route("/admin/form", methods=["GET", "POST"])
def admin_form():
    template = """
    <!doctype html>
    <html lang="en">
    <head>
        <meta charset="utf-8" />
        <title>QR Redirect Admin</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 2rem; }
            form { margin-bottom: 2rem; }
            label { display: block; margin-bottom: 0.5rem; }
            input { padding: 0.5rem; width: 24rem; max-width: 100%; margin-bottom: 1rem; }
            button { padding: 0.5rem 1rem; cursor: pointer; }
            .result { border: 1px solid #ccc; padding: 1.5rem; max-width: 30rem; }
            .qr { margin-top: 1rem; }
            .error { color: #b00020; }
        </style>
    </head>
    <body>
        <h1>Create or Update Redirect</h1>
        <form method="post">
            <label>
                Unique ID:
                <input type="text" name="unique_id" value="{{ unique_id|default('') }}" required />
            </label>
            <label>
                Final URL:
                <input type="url" name="final_url" value="{{ final_url|default('') }}" required />
            </label>
            <button type="submit">Submit</button>
        </form>

        {% if error %}
            <p class="error">{{ error }}</p>
        {% endif %}

        {% if result %}
        <div class="result">
            <p>Status: <strong>{{ result.status }}</strong></p>
            <p>Redirect URL: <a href="{{ result.redirect_url }}">{{ result.redirect_url }}</a></p>
            <p>Final URL: <a href="{{ result.final_url }}">{{ result.final_url }}</a></p>
            <div class="qr">
                <img src="data:image/png;base64,{{ result.qr_code_base64 }}" alt="QR Code" />
            </div>
        </div>
        {% endif %}

        <p><a href="{{ url_for('view_entries') }}">View all entries</a></p>
        {% if unique_id %}
            <p><a href="{{ url_for('serve_qr_code', unique_id=unique_id) }}">Direct QR image for {{ unique_id }}</a></p>
        {% endif %}
    </body>
    </html>
    """

    context = {"unique_id": "", "final_url": "", "result": None, "error": None}

    if request.method == "POST":
        unique_id = request.form.get("unique_id", "").strip()
        final_url = request.form.get("final_url", "").strip()
        context["unique_id"] = unique_id
        context["final_url"] = final_url

        try:
            entry, redirect_url, status = _register_redirect(unique_id, final_url)
            qr_code_b64 = _encode_qr_code(redirect_url)
            context["result"] = {
                "status": status,
                "redirect_url": redirect_url,
                "final_url": entry["final_url"],
                "qr_code_base64": qr_code_b64,
            }
        except (ValueError, TypeError) as exc:
            context["error"] = str(exc)

    return render_template_string(template, **context)


@app.route("/qr/<unique_id>", methods=["GET"])
def serve_qr_code(unique_id: str):
    entry = redirect_map.get(unique_id)
    if not entry:
        return jsonify({"error": "unique_id not found"}), 404
    redirect_url = _build_redirect_url(entry["redirect_slug"])
    buffer = _generate_qr_png(redirect_url)
    return send_file(
        buffer,
        mimetype="image/png",
        as_attachment=False,
        download_name=f"{unique_id}.png",
    )


@app.route("/admin/entries", methods=["GET"])
def view_entries():
    template = """
    <!doctype html>
    <html lang="en">
    <head>
        <meta charset="utf-8" />
        <title>Redirect Map</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 2rem; }
            table { border-collapse: collapse; width: 100%; max-width: 60rem; }
            th, td { border: 1px solid #ccc; padding: 0.5rem 0.75rem; text-align: left; }
            tr:nth-child(even) { background: #f7f7f7; }
        </style>
    </head>
    <body>
        <h1>Registered Redirects</h1>
        {% if entries %}
        <table>
            <thead>
                <tr>
                    <th>Unique ID</th>
                    <th>Redirect URL</th>
                    <th>Final URL</th>
                    <th>QR Code</th>
                </tr>
            </thead>
            <tbody>
            {% for unique_id, entry in entries %}
                <tr>
                    <td>{{ unique_id }}</td>
                    <td><a href="{{ entry.redirect_url }}">{{ entry.redirect_url }}</a></td>
                    <td><a href="{{ entry.final_url }}">{{ entry.final_url }}</a></td>
                    <td><a href="{{ url_for('serve_qr_code', unique_id=unique_id) }}">QR</a></td>
                </tr>
            {% endfor %}
            </tbody>
        </table>
        {% else %}
            <p>No entries registered yet.</p>
        {% endif %}

        <p><a href="{{ url_for('admin_form') }}">Back to form</a></p>
    </body>
    </html>
    """
    rows = []
    for unique_id, entry in redirect_map.items():
        rows.append(
            (
                unique_id,
                {
                    "redirect_url": _build_redirect_url(entry["redirect_slug"]),
                    "final_url": entry["final_url"],
                },
            )
        )
    rows.sort(key=lambda row: row[0])
    return render_template_string(template, entries=rows)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))

