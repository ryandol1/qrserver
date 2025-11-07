from flask import Flask, request, send_file, abort
import segno
import io

app = Flask(__name__)

@app.route("/qr")
def generate_qr():
    data = request.args.get("data")
    size = int(request.args.get("size", 300))

    if not data:
        abort(400, "Missing ?data parameter")

    # Generate QR as PNG in memory
    qr = segno.make(data)
    img_io = io.BytesIO()
    qr.save(img_io, kind="png", scale=size // 29)  # ~29px per module for 300px total
    img_io.seek(0)

    return send_file(img_io, mimetype="image/png")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
